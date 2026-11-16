import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from dataquery_agent.document_ingestion import ExtractionError, MAX_BYTES, MAX_PAGES, extract_pdf

SAMPLE = Path(__file__).resolve().parents[1] / "output/pdf/document-extraction-sample.pdf"
OWNER = "document-extraction-synthetic-fixture"


@unittest.skipUnless(importlib.util.find_spec("pypdf"), "Install requirements-pdf.txt")
class PdfExtractionTests(unittest.TestCase):
    def test_refuses_active_and_embedded_content_and_encryption(self):
        from pypdf import PdfWriter
        from pypdf.actions import JavaScript

        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.pdf"
            for action, error in (
                (lambda doc: doc.add_open_action(JavaScript("app.alert('fixture')")), "active_or_embedded_pdf_content"),
                (lambda doc: doc.add_attachment("fixture.txt", b"sample"), "active_or_embedded_pdf_content"),
                (lambda doc: doc.encrypt("fixture"), "encrypted_pdf"),
            ):
                with self.subTest(error=error):
                    writer = PdfWriter()
                    writer.add_blank_page(width=100, height=100)
                    action(writer)
                    writer.write(path)
                    with self.assertRaisesRegex(ExtractionError, error):
                        extract_pdf(path, owner=OWNER)

    def test_file_page_and_owner_limits(self):
        from pypdf import PdfWriter

        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.pdf"
            with path.open("wb") as file:
                file.truncate(MAX_BYTES + 1)
            with self.assertRaisesRegex(ExtractionError, "pdf_size_limit"):
                extract_pdf(path, owner=OWNER)
            writer = PdfWriter()
            for _ in range(MAX_PAGES + 1):
                writer.add_blank_page(width=100, height=100)
            writer.write(path)
            with self.assertRaisesRegex(ExtractionError, "pdf_page_limit"):
                extract_pdf(path, owner=OWNER)
            with self.assertRaisesRegex(ExtractionError, "owner_scope_required"):
                extract_pdf(path, owner="short")

    @unittest.skipUnless(sys.platform == "darwin" and os.getenv("DOCUMENT_OCR_TESTS") == "1",
                         "Requires the macOS Vision integration")
    def test_native_scan_and_blank_pages(self):
        from hashlib import sha256
        from pypdf import PdfReader

        reader = PdfReader(SAMPLE)
        self.assertFalse(reader.pages[1].extract_text().strip())
        result = extract_pdf(SAMPLE, owner=OWNER)
        self.assertEqual(result["file_sha256"], sha256(SAMPLE.read_bytes()).hexdigest())
        self.assertEqual(result["review_state"], "unreviewed")
        native, scan, blank = result["pages"]
        self.assertEqual((native["page"], native["method"], native["status"]), (1, "native_text", "ok"))
        self.assertIn("本地样本：订单数量", native["text"])
        self.assertIn("Order count: 24", native["text"])
        self.assertEqual((scan["page"], scan["method"], scan["status"]), (2, "ocr", "ok"))
        self.assertIn("扫描样本", scan["text"])
        self.assertIn("订单数量", scan["text"])
        self.assertIn("Order count: 48", scan["text"])
        self.assertTrue(scan["blocks"])
        self.assertAlmostEqual(scan["blocks"][0]["bounds"][0], 42 / 520, delta=0.025)
        for block in scan["blocks"]:
            x, y, w, h = block["bounds"]
            self.assertTrue(0 <= x < x + w <= 1 and 0 <= y < y + h <= 1)
        self.assertEqual((blank["page"], blank["status"], blank["text"]), (3, "no_text", ""))

    def test_native_document_identity_is_scoped(self):
        from pypdf import PdfWriter

        with TemporaryDirectory() as directory:
            native = Path(directory) / "native.pdf"
            writer = PdfWriter()
            writer.append(SAMPLE, pages=(0, 1))
            writer.write(native)
            first = extract_pdf(native, owner=OWNER)
            second = extract_pdf(native, owner="another-synthetic-extraction-owner")
            self.assertEqual(first["document_version"], second["document_version"])
            self.assertNotEqual(first["document_id"], second["document_id"])
            self.assertEqual(first, extract_pdf(native, owner=OWNER))
