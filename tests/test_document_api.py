import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import json
import os
from pathlib import Path
from secrets import token_hex
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx2
import uvicorn

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.document_ingestion import MAX_BYTES
from dataquery_agent.document_staging import DocumentStaging
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_business_knowledge import FixedEmbedding
from test_api import AUTH, TOKEN, client
from test_pdf_knowledge import PDF

BASE = "/api/documents"
PDF_HEADERS = AUTH | {"Content-Type": "application/pdf"}


def native_sample():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_page(PdfReader(PDF).pages[0])
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


@asynccontextmanager
async def loopback(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", proxy_headers=False, ws="none"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(.02)
            async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=85) as http:
                yield http
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 5)


class DocumentApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_boundary(self):
        with TemporaryDirectory() as directory:
            staging = DocumentStaging(Path(directory))
            app = create_app(token=TOKEN, manager_factory=lambda: (None, token_hex(32)), document_staging=staging)
            async with client(app) as http:
                self.assertEqual((await http.post(BASE, content=b"%PDF-")).status_code, 401)
                self.assertEqual((await http.post(BASE, content=b"%PDF-", headers=AUTH)).status_code, 415)
                self.assertEqual((await http.post(BASE, content=b"fake.pdf", headers=PDF_HEADERS)).json()["error"]["code"],
                                 "invalid_pdf_header")
                async def chunks():
                    for _ in range(11):
                        yield b"x" * (MAX_BYTES // 10)
                self.assertEqual((await http.post(BASE, content=chunks(), headers=PDF_HEADERS)).status_code, 413)
                self.assertEqual((await http.get(BASE + "/%2E%2E%2F.env", headers=AUTH)).status_code, 404)
            async with client(app, address="192.0.2.2") as http:
                self.assertEqual((await http.post(BASE, content=b"%PDF-", headers=PDF_HEADERS)).status_code, 403)


@unittest.skipUnless(os.getenv("DOCUMENT_RAG_POSTGRES_TESTS") == "1", "PostgreSQL, Apple Vision and PDF opt-in")
class DocumentApiPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_http_lifecycle(self):
        manager, _ = local_manager()
        owner, other = token_hex(32), token_hex(32)
        writer = KnowledgeStore.local(admin=True)
        real = os.getenv("DOCUMENT_API_GEMINI") == "1"
        embedding = MeasuredEmbedding.local() if real else FixedEmbedding()
        manager.agent.knowledge.retriever.embedding = embedding
        if not real:
            def generate(prompt):
                evidence = json.loads(prompt.split("\n", 1)[1])["evidence"]
                item = next(row for row in evidence if "Order count: 48" in row["text"])
                return json.dumps({"status": "supported", "claims": [{"chunk_id": item["chunk_id"], "quote": "Order count: 48"}]})
            manager.generate = generate

        with TemporaryDirectory() as directory:
            stage = DocumentStaging(Path(directory), publisher=writer, embedding=embedding)
            def app(identity):
                return create_app(token=TOKEN, manager_factory=lambda: (manager, identity), document_staging=stage)

            async def expect(response, status=200):
                self.assertEqual(response.status_code, status, response.text)
                return response.json()

            try:
                async with loopback(app(owner)) as http:
                    native = await expect(await http.post(BASE, content=native_sample(), headers=PDF_HEADERS), 201)
                    self.assertEqual(native["pages"][0]["method"], "native_text")
                    raw = PDF.read_bytes()
                    imported = await expect(await http.post(BASE, content=raw, headers=PDF_HEADERS), 201)
                    url = BASE + "/" + imported["document_id"]
                    self.assertEqual(imported["state"], "unreviewed")
                    self.assertEqual([row["status"] for row in imported["pages"]], ["ok", "ok", "no_text"])
                    from pypdf import PdfWriter
                    from pypdf.actions import JavaScript
                    for count, script, error in ((1, True, "active_or_embedded_pdf_content"),
                                                  (41, False, "pdf_page_limit")):
                        candidate = PdfWriter()
                        for _ in range(count):
                            candidate.add_blank_page(width=100, height=100)
                        if script:
                            candidate.add_open_action(JavaScript("app.alert('test')"))
                        encoded = BytesIO()
                        candidate.write(encoded)
                        blocked = await expect(await http.post(BASE, content=encoded.getvalue(), headers=PDF_HEADERS), 422)
                        self.assertEqual(blocked["error"]["code"], error)

                    ocr = (await expect(await http.get(url + "/pages/2", headers=AUTH)))["page"]
                    self.assertEqual(ocr["method"], "ocr")
                    self.assertTrue(ocr["blocks"])
                    self.assertIn("confidence", ocr["blocks"][0])
                    review = {"version": imported["version"], "title": "Synthetic PDF verification",
                              "basis": "Compared against the authorized synthetic original",
                              "corrections": {"2": ocr["text"] + "\nReviewed synthetic OCR line."}}
                    seal = {"version": imported["version"], "review_version": "pdf-" + "0" * 48,
                            "external_embedding_approved": True}
                    await expect(await http.post(url + "/publish", json=seal, headers=AUTH), 409)
                    await expect(await http.post(url + "/review", json=review | {"reviewed": True}, headers=AUTH), 422)
                    approved = await expect(await http.post(url + "/review", json=review, headers=AUTH))
                    self.assertEqual(approved["state"], "reviewed")
                    self.assertNotIn("signature", approved)
                    self.assertNotIn("owner_scope", approved)
                    reviewed = (await expect(await http.get(url + "/pages/2", headers=AUTH)))["page"]
                    self.assertEqual(reviewed["text"], ocr["text"])
                    self.assertEqual(reviewed["reviewed_text"], review["corrections"]["2"])
                    seal["review_version"] = approved["review_version"]
                    await expect(await http.post(url + "/publish", json=seal | {"signature": "forged"}, headers=AUTH), 422)
                    await expect(await http.post(url + "/publish", json=seal | {"external_embedding_approved": False},
                                                 headers=AUTH), 422)
                    with stage.connect(write=True) as db:
                        receipt = db.execute("SELECT receipt FROM reviews WHERE upload=?", (seal["version"],)).fetchone()[0]
                        forged = json.loads(receipt) | {"signature": "0" * 64}
                        db.execute("UPDATE reviews SET receipt=? WHERE upload=?", (json.dumps(forged), seal["version"]))
                    rejected = await expect(await http.post(url + "/publish", json=seal, headers=AUTH), 409)
                    self.assertEqual(rejected["error"]["code"], "invalid_review_signature")
                    with stage.connect(write=True) as db:
                        db.execute("UPDATE reviews SET receipt=? WHERE upload=?", (receipt, seal["version"]))
                    with patch.object(embedding, "embed", side_effect=TimeoutError):
                        await expect(await http.post(url + "/publish", json=seal, headers=AUTH), 503)
                    self.assertEqual((await expect(await http.get(url, headers=AUTH)))["state"], "reviewed")
                    self.assertFalse(writer.list_versions(owner=owner))

                    published = await expect(await http.post(url + "/publish", json=seal, headers=AUTH))
                    duplicate = await expect(await http.post(url + "/publish", json=seal, headers=AUTH))
                    self.assertTrue(published["publication"]["indexed"])
                    self.assertFalse(duplicate["publication"]["indexed"])
                    self.assertEqual(published["publication"]["version"], duplicate["publication"]["version"])
                    async with client(app(other)) as foreign:
                        await expect(await foreign.get(url + "/pages/2", headers=AUTH), 404)
                        await expect(await foreign.post(url + "/review", json=review, headers=AUTH), 404)
                        await expect(await foreign.post(url + "/publish", json=seal, headers=AUTH), 404)
                        await expect(await foreign.post(url + "/versions", content=raw, headers=PDF_HEADERS), 404)
                    conversation = await expect(await http.post("/api/conversations", headers=AUTH), 201)
                    response = await expect(await http.post("/api/chat", headers=AUTH, json={
                        "conversation_id": conversation["conversation_id"], "message_id": str(uuid4()),
                        "question": "PDF 中带有 Scanned verification page 标题的页面，请逐字摘录该页的 Order count 那一整行。",
                    }))
                    citation = response["citations"][0]
                    self.assertEqual((citation["page_number"], citation["quote"]), (2, "Order count: 48"))
                    self.assertEqual(citation["source_hash"], imported["file_sha256"])
                    self.assertEqual(citation["document_version"], published["publication"]["version"])

                    revised = await expect(await http.post(url + "/review", json=review | {
                        "basis": "Second comparison against the synthetic original"}, headers=AUTH))
                    self.assertNotEqual(revised["review_version"], seal["review_version"])
                    rejected = await expect(await http.post(url + "/publish", json=seal, headers=AUTH), 409)
                    self.assertEqual(rejected["error"]["code"], "current_review_required")
                    with stage.connect(write=True) as db:
                        db.execute("UPDATE reviews SET receipt=? WHERE upload=? AND version=?",
                                   (receipt, seal["version"], revised["review_version"]))
                    stale_receipt = await expect(await http.post(url + "/publish", json=seal | {
                        "review_version": revised["review_version"]}, headers=AUTH), 409)
                    self.assertEqual(stale_receipt["error"]["code"], "review_source_mismatch")
                    replacement = await expect(await http.post(url + "/versions", content=native_sample(),
                                                                 headers=PDF_HEADERS), 201)
                    self.assertEqual(replacement["document_id"], imported["document_id"])
                    self.assertNotEqual(replacement["version"], imported["version"])
                    self.assertEqual(replacement["state"], "unreviewed")
                    rejected = await expect(await http.post(url + "/publish", json=seal, headers=AUTH), 409)
                    self.assertEqual(rejected["error"]["code"], "stale_upload_version")
                if path := os.getenv("DOCUMENT_API_REPORT"):
                    from hashlib import sha256
                    trace = manager.store.history(owner, response["query_id"])
                    Path(path).write_text(json.dumps({
                        "real_gemini": real, "transport": "HTTP over loopback", "native": native,
                        "import": imported, "review": approved, "publish": published["publication"],
                        "republish": duplicate["publication"], "citation": citation, "trace": response["trace"],
                        "history_citation_matches": trace["knowledge"]["citations"][0]["quote"] == citation["quote"],
                        "corrections_preserved": True, "original_matches_file": sha256(raw).hexdigest() == imported["file_sha256"],
                        "embedding_batches": embedding.batches if real else [],
                        "rejections": ["unreviewed", "forged_signature", "cross_owner", "stale_review", "stale_upload"],
                        "publish_failure_left_unpublished": True,
                    }, ensure_ascii=False, indent=2))
            finally:
                for row in writer.list_versions(owner=owner):
                    writer.delete(row["document_id"], owner=owner)
            self.assertFalse(any(row["deleted_at"] is None for row in writer.list_versions(owner=owner)))
