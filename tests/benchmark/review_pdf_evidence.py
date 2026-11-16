"""Exercise the repository's authorized synthetic PDF with local OCR and pgvector."""
from datetime import date
from hashlib import sha256
from secrets import token_hex

from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag.answer import build_evidence, validate_claim
from dataquery_agent.rag.documents import KnowledgeError
from dataquery_agent.rag.pdf_review import approve
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_business_knowledge import FixedEmbedding

from .dataset import ROOT

SOURCE = "output/pdf/document-extraction-sample.pdf"
REVIEWER = "Codex technical evidence review"
TODAY = date(2026, 11, 17)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def collect_pdf():
    owner, other = token_hex(32), token_hex(32)
    writer, reader = KnowledgeStore.local(admin=True), KnowledgeStore.local()
    embed = FixedEmbedding()
    retriever = Retriever(reader, embed)
    pdf = ROOT / SOURCE
    extraction = extract_pdf(pdf, owner=owner)
    raw_hash = sha256(pdf.read_bytes()).hexdigest()
    require(extraction["file_sha256"] == raw_hash, "original hash disagrees with extraction")
    pages = {page["page"]: page for page in extraction["pages"]}
    require(set(pages) == {1, 2, 3}, "fixture PDF page inventory changed")
    require(pages[1]["method"] == "native_text" and "Order count: 24" in pages[1]["text"], "native page drift")
    require(pages[2]["method"] == "ocr" and "Order count: 48" in pages[2]["text"], "OCR page drift")
    require(pages[3]["status"] == "no_text" and not pages[3]["text"], "blank page drift")
    slug = "golden-evidence-" + token_hex(10)

    def publish(text, basis):
        receipt = approve(extraction, pdf, owner=owner, slug=slug, title="Order count PDF evidence verification",
                          basis=basis, corrections={"2": text})
        info = writer.index(None, embed, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=pdf)
        return info, receipt["review"]

    def search(scope):
        rows, _ = retriever.search("Order count verification page", today=TODAY, owner=scope, reviewed_pdf=True)
        return rows

    def cite(evidence, page, quote):
        item = next(item for item in evidence if item["page_number"] == page)
        return validate_claim({"chunk_id": item["chunk_id"], "quote": quote}, {item["chunk_id"]: item})

    require(not search(owner), "fresh Owner unexpectedly has published records")
    try:
        writer.index(None, embed, owner=owner, external_approved=True, pdf_receipt=extraction, pdf=pdf)
    except KnowledgeError as error:
        require(str(error) == "signed_review_required", "unexpected unsigned review behavior")
    else:
        raise ValueError("unsigned PDF unexpectedly published")
    doc_id = None
    try:
        first, _ = publish("扫描样本：订单数量\nOrder count: 4B", "Synthetic seeded transcription error for version denial")
        doc_id = first["document_id"]
        previous, _, _ = build_evidence(search(owner), {})
        stale = cite(previous, 2, "Order count: 4B")
        require(reader.current_pdf_citations([stale], owner=owner) == [stale], "initial citation is not active")
        current, review = publish("扫描样本：订单数量\nOrder count: 48", "Checked corrected number against PDF page 2")
        rows = search(owner)
        evidence, _, _ = build_evidence(rows, {})
        citations = [cite(evidence, 1, "Order count: 24"), cite(evidence, 2, "Order count: 48")]
        require(len(rows) == 2 and {item["page_number"] for item in evidence} == {1, 2}, "unexpected PDF pages indexed")
        require(all(row["document_id"] == doc_id and row["document_version"] == current["version"]
                    and row["publication_state"] == "published" and row["document_hash"] == raw_hash
                    for row in rows), "published version or hash mismatch")
        require(reader.current_pdf_citations(citations, owner=owner) == citations, "current citations rejected")
        require(not reader.current_pdf_citations([stale], owner=owner), "superseded citation still valid")
        require(not search(other) and not reader.current_pdf_citations(citations, owner=other), "cross-Owner evidence")
        revised = citations[1]
        require((revised["line_start"], revised["line_end"], revised["extraction_method"]) == (1, 2, "ocr")
                and citations[0]["extraction_method"] == "native_text"
                and not {"bounds", "confidence"}.intersection(revised), "OCR correction has invalid location")
        require(any(block.get("confidence") is not None for block in pages[2]["blocks"]), "OCR confidence fixture missing")
        require(review["pages"][1]["corrected"] and review["pages"][1]["extracted_text_sha256"] !=
                review["pages"][1]["reviewed_text_sha256"], "expected a revised OCR page")
        review_pages = [{key: value for key, value in page.items() if key != "text"} for page in review["pages"]]
    finally:
        if doc_id:
            require(writer.delete(doc_id, owner=owner), "temporary evidence withdrawal failed")
    require(not search(owner) and not reader.current_pdf_citations(citations, owner=owner), "withdrawn evidence still visible")
    return {"source": SOURCE, "source_type": "repository_synthetic_pdf", "original_sha256": raw_hash,
            "reviewer": REVIEWER, "review_version": review["review_version"], "extraction_version": extraction["extraction_version"],
            "document_id": doc_id, "verified_version": current["version"], "publication_state_during_check": "published",
            "owner_access": "same owner only", "embedding_mode": embed.version,
            "page_inventory": [{"page": n, "method": page["method"], "status": page["status"]} for n, page in pages.items()],
            "reviewed_pages": review_pages, "active_citations_at_verification": citations, "superseded_citation": stale,
            "checks": {"unsigned_rejected": True, "blank_page_not_indexed": True, "foreign_owner_rejected": True,
                       "old_citation_rejected": True, "ocr_confidence_only_in_extraction": True,
                       "revised_citation_uses_reviewed_lines": True, "withdrawn_citations_rejected": True}}
