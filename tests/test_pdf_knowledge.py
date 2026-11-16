"""Exercise reviewed PDF evidence through the live conversation and RLS stores."""
from datetime import date
import json
import os
from pathlib import Path
from secrets import token_hex
from uuid import uuid4
import unittest

from dataquery_agent.api_models import public_response
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import ConversationError
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag.pdf_review import approve
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_business_knowledge import FixedEmbedding

PDF = Path(__file__).resolve().parents[1] / "output/pdf/document-extraction-sample.pdf"
TODAY = date(2026, 11, 16)


@unittest.skipUnless(os.getenv("DOCUMENT_RAG_POSTGRES_TESTS") == "1", "Requires pgvector and macOS OCR")
class PdfKnowledgePostgresTests(unittest.TestCase):
    def test_agent_history_and_current_pdf_evidence(self):
        owner, other = token_hex(32), token_hex(32)
        manager, _ = local_manager()
        writer, embedding = KnowledgeStore.local(admin=True), FixedEmbedding()
        manager.agent.knowledge.retriever.embedding = embedding
        cid = manager.create_conversation(owner)["conversation_id"]
        other_cid = manager.create_conversation(other)["conversation_id"]
        slug = "pdf-agent-" + token_hex(10)
        extraction = extract_pdf(PDF, owner=owner)
        doc_id = None

        def proposal(quote, after=None):
            def generate(prompt):
                evidence = json.loads(prompt.split("\n", 1)[1])["evidence"]
                item = next((item for item in evidence if quote in item["text"]), evidence[0])
                if after:
                    after()
                return json.dumps({"status": "supported",
                                   "claims": [{"chunk_id": item["chunk_id"], "quote": quote}]})
            return generate

        def ask(question, quote="Order count: 48", *, user=owner, conversation=cid, after=None):
            manager.generate = proposal(quote, after)
            answer = manager.process_message(user, conversation, question, uuid4(), today=TODAY)
            self.assertEqual(answer["status"], "ok", answer)
            return answer

        def publish(corrections=None):
            receipt = approve(extraction, PDF, owner=owner, slug=slug, title="Order count PDF verification",
                              basis="Compared with the synthetic original", corrections=corrections or {})
            return writer.index(None, embedding, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=PDF)

        question = "PDF 扫描页文档中 Order count 的原文"
        try:
            self.assertEqual(ask(question)["result"]["evidence_status"], "insufficient")
            first = publish()
            doc_id = first["document_id"]
            native = ask("PDF 原生文本页 Order count 的原文", "Order count: 24")
            self.assertEqual(native["result"]["citations"][0]["page_number"], 1)
            self.assertEqual(native["result"]["citations"][0]["extraction_method"], "native_text")
            answer = ask("文档扫描样本中 Order count 的原文")
            citation = answer["result"]["citations"][0]
            self.assertEqual((citation["page_number"], citation["line_start"], citation["line_end"]), (2, 1, 3))
            self.assertEqual(citation["source_hash"], extraction["file_sha256"])
            self.assertEqual(citation["extraction_method"], "ocr")
            self.assertEqual(citation["access_scope"], "owner")
            self.assertIn("原文摘录", answer["answer_zh"])
            self.assertIn("PDF 第 2 页", answer["answer_zh"])

            history = manager.get_query(owner, answer["query_id"])
            self.assertEqual(history["knowledge"]["evidence_status"], "supported")
            self.assertEqual(history["knowledge"]["citations"], [citation])
            replay = public_response(history, history=True)
            live = public_response(answer)
            self.assertEqual(replay.citations, live.citations)
            self.assertEqual(replay.trace, live.trace)
            self.assertTrue(live.trace.tools[0].includes_reviewed_pdf)
            self.assertEqual(live.trace.tools[0].task_id, live.trace.tasks[0].task_id)
            self.assertEqual(live.trace.tools[0].plan_id, live.trace.plan_id)
            self.assertEqual(live.trace.tools[0].attempt_id, live.trace.attempt_id)
            with self.assertRaises(ConversationError):
                manager.get_query(other, answer["query_id"])
            foreign = ask(question, user=other, conversation=other_cid)
            self.assertEqual(foreign["result"]["citations"], [])
            self.assertFalse(foreign["trace"]["model_attempts"])

            forged = ask(question, "Order count: 999")
            self.assertEqual(forged["result"]["citations"], [])
            self.assertEqual(forged["trace"]["model_attempts"][0]["status"], "invalid_output")
            exact = ask("PDF 里订单量的正式定义是什么？")
            self.assertFalse(exact["result"]["retrieval_trace"]["query_embedding_called"])
            self.assertEqual(exact["result"]["retrieval_trace"]["strategy"], "catalog_exact")

            switched = {}
            def supersede():
                switched.update(publish({"2": "扫描样本：订单数量\nOrder count: 48"}))

            stale = ask(question, after=supersede)
            self.assertEqual(stale["result"]["citations"], [])
            self.assertEqual(stale["result"]["evidence_status"], "insufficient")
            current = ask(question)["result"]["citations"][0]
            self.assertEqual(current["document_version"], switched["version"])
            self.assertNotEqual(current["document_version"], citation["document_version"])
            self.assertEqual(current["source_location"], "page:2;lines:1-2")
            self.assertNotIn("bounds", current)
        finally:
            if doc_id:
                writer.delete(doc_id, owner=owner)
        self.assertEqual(ask(question)["result"]["citations"], [])
