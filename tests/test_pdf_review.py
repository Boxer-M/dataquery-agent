import json
import os
from copy import deepcopy
from datetime import date
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag.answer import build_evidence, validate_claim
from dataquery_agent.rag.documents import KnowledgeError
from dataquery_agent.rag.pdf_review import approve, build_document, verify_receipt
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import CHUNKS, KnowledgeStore
from evaluate_business_knowledge import FixedEmbedding

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "output/pdf/document-extraction-sample.pdf"
OWNER_FIXTURE = "document-extraction-synthetic-fixture"
TODAY = date(2026, 11, 16)


class PdfReviewTests(unittest.TestCase):
    def setUp(self):
        self.extraction = json.loads((ROOT / "reports/warehouse/document_extraction_sample.json").read_text())
        self.signer = patch("dataquery_agent.rag.pdf_review.reviewer_key", return_value=bytes(range(32)))
        self.signer.start()
        self.addCleanup(self.signer.stop)

    def review(self, extraction, pdf=PDF, *, owner=OWNER_FIXTURE, slug="fixture", corrections=None):
        return approve(extraction, pdf, owner=owner, slug=slug, title="Synthetic PDF verification",
                       basis="Compared approved text with the original PDF", corrections=corrections or {})

    def test_rejects_unsigned_tampered_cross_owner_and_unavailable(self):
        engine = Mock()
        writer = KnowledgeStore("dbname=sqlshed user=sqlshed_knowledge_admin", admin=True)
        with patch("dataquery_agent.rag.pdf_review.extract_pdf", return_value=self.extraction):
            receipt = self.review(self.extraction)
            forged = deepcopy(self.extraction)
            forged["review_state"] = "reviewed"
            with self.assertRaisesRegex(KnowledgeError, "signed_review_required"):
                writer.index(None, engine, owner=OWNER_FIXTURE, pdf_receipt=forged, pdf=PDF, external_approved=True)
            with self.assertRaisesRegex(KnowledgeError, "signed_review_required"):
                writer.index(build_document(receipt["review"]), engine, owner=OWNER_FIXTURE, external_approved=True)
            with self.assertRaisesRegex(KnowledgeError, "review_owner_mismatch"):
                self.review(self.extraction, owner=token_hex(32))
            with self.assertRaisesRegex(KnowledgeError, "review_owner_mismatch"):
                writer.index(None, engine, owner=token_hex(32), pdf_receipt=receipt, pdf=PDF, external_approved=True)
            with self.assertRaisesRegex(KnowledgeError, "external_embedding_confirmation_required"):
                writer.index(None, engine, owner=OWNER_FIXTURE, pdf_receipt=receipt, pdf=PDF)
            changed = deepcopy(receipt)
            changed["review"]["pages"][0]["text"] = "Unsigned modification"
            with self.assertRaisesRegex(KnowledgeError, "invalid_review_signature"):
                writer.index(None, engine, owner=OWNER_FIXTURE, pdf_receipt=changed, pdf=PDF, external_approved=True)
            with self.assertRaisesRegex(KnowledgeError, "invalid_review_corrections"):
                self.review(self.extraction, corrections={"3": "Blank page must remain blank"})
        engine.embed.assert_not_called()

    def test_review_revision_tracks_text_hash_and_locations(self):
        with patch("dataquery_agent.rag.pdf_review.extract_pdf", return_value=self.extraction):
            first = self.review(self.extraction)
            second = self.review(self.extraction, corrections={"2": "扫描样本：订单数量\nOrder count: 48"})
        original, original_meta = verify_receipt(first, owner=OWNER_FIXTURE, pdf=PDF)
        revised, revised_meta = verify_receipt(second, owner=OWNER_FIXTURE, pdf=PDF)
        self.assertEqual(original.content_hash, revised.content_hash)
        self.assertNotEqual(original.version, revised.version)
        self.assertEqual([p.location for p in revised.passages], ["page:1;lines:1-3", "page:2;lines:1-2"])
        self.assertEqual([p["method"] for p in revised_meta["pages"]], ["native_text", "ocr"])
        self.assertNotEqual(original_meta["pages"][1]["reviewed_text_sha256"],
                            revised_meta["pages"][1]["reviewed_text_sha256"])
        self.assertEqual(original_meta["pages"][1]["extracted_text_sha256"],
                         revised_meta["pages"][1]["extracted_text_sha256"])


@unittest.skipUnless(os.getenv("DOCUMENT_RAG_POSTGRES_TESTS") == "1", "Requires pgvector and macOS OCR")
class PdfReviewPostgresTests(unittest.TestCase):
    def test_publish_retrieve_supersede_and_isolate(self):
        from pypdf import PdfReader, PdfWriter

        owner, other = token_hex(32), token_hex(32)
        slug = "synthetic-pdf-" + token_hex(10)
        writer, reader = KnowledgeStore.local(admin=True), KnowledgeStore.local()
        engine = Mock(wraps=FixedEmbedding())
        engine.model, engine.version = FixedEmbedding.model, FixedEmbedding.version
        query = Retriever(reader, engine)
        doc_id = None

        def publish(receipt, pdf):
            return writer.index(None, engine, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=pdf)

        def search(scope=owner, **options):
            return query.search("Order count", today=TODAY, owner=scope, **options)[0]

        def review(pdf, corrections=None):
            return approve(extract_pdf(pdf, owner=owner), pdf, owner=owner, slug=slug,
                           title="Order count verification", basis="Compared to the synthetic original",
                           corrections=corrections or {})

        with patch("dataquery_agent.rag.pdf_review.reviewer_key", return_value=bytes(range(32))):
            try:
                receipt = review(PDF)
                first = publish(receipt, PDF)
                doc_id = first["document_id"]
                self.assertEqual(first["chunks"], 2)
                self.assertFalse(publish(receipt, PDF)["indexed"])
                engine.embed.assert_called_once()
                rows = search(reviewed_pdf=True)
                self.assertEqual(len(rows), 2)
                self.assertEqual({row["document_version"] for row in rows}, {first["version"]})
                self.assertEqual({row["publication_state"] for row in rows}, {"published"})
                self.assertEqual({row["source_location"].split(";")[0] for row in rows}, {"page:1", "page:2"})
                self.assertFalse(search(other, reviewed_pdf=True))
                self.assertFalse(any(row["document_id"] == doc_id for row in search()))
                evidence, _, _ = build_evidence(rows, {})
                scanned = next(item for item in evidence if item["source_location"].startswith("page:2;"))
                self.assertEqual(validate_claim({"chunk_id": scanned["chunk_id"], "quote": "Order count: 48"},
                                                {scanned["chunk_id"]: scanned})["source_location"],
                                 "page:2;lines:1-3")
                with reader.transaction(other) as pg:
                    self.assertFalse(pg.execute(f"SELECT 1 FROM {CHUNKS} WHERE document_id=%s", (doc_id,)).fetchall())
                with reader.transaction(owner) as pg:
                    rows = pg.execute(f"SELECT rag_vector.vector_dims(embedding) AS dim FROM {CHUNKS} "
                                      "WHERE document_id=%s", (doc_id,)).fetchall()
                    self.assertEqual(rows, [{"dim": 768}, {"dim": 768}])

                revised = publish(review(PDF, corrections={"2": "Order count: 48\n扫描样本：订单数量"}), PDF)
                self.assertEqual(revised["document_id"], doc_id)
                self.assertNotEqual(revised["version"], first["version"])
                self.assertEqual({row["document_version"] for row in search(reviewed_pdf=True)}, {revised["version"]})
                with TemporaryDirectory() as directory:
                    replacement = Path(directory) / "replacement.pdf"
                    pdf = PdfWriter(clone_from=PdfReader(PDF))
                    pdf.add_metadata({"/Title": "Versioned synthetic original"})
                    pdf.write(replacement)
                    with self.assertRaisesRegex(KnowledgeError, "review_source_mismatch"):
                        publish(receipt, replacement)
                    updated = review(replacement)
                    latest = publish(updated, replacement)
                    self.assertEqual(latest["document_id"], doc_id)
                    self.assertNotEqual(updated["review"]["file_sha256"], receipt["review"]["file_sha256"])
                    self.assertNotEqual(latest["version"], revised["version"])
                with self.assertRaisesRegex(KnowledgeError, "cannot_reactivate_superseded_version"):
                    publish(receipt, PDF)
                self.assertEqual({row["document_version"] for row in search(reviewed_pdf=True)}, {latest["version"]})
                self.assertEqual(len(writer.chunks(doc_id, owner=owner)), 6)
                self.assertFalse(writer.chunks(doc_id, owner=other))
            finally:
                if doc_id:
                    writer.delete(doc_id, owner=owner)
