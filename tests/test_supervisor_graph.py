import importlib.util
import json
import os
from secrets import token_hex
from uuid import uuid4
import unittest

from dataquery_agent.agent_runtime import AgentRuntime
from dataquery_agent.api_models import public_response
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag.pdf_review import approve
from dataquery_agent.rag.store import KnowledgeStore
from dataquery_agent.supervisor_graph import role_context, run_supervisor
from evaluate_business_knowledge import FixedEmbedding
from test_business_planner_v2 import PROPOSE
from test_pdf_knowledge import PDF, TODAY

HAS_GRAPH = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(HAS_GRAPH, "Install requirements-langgraph.txt")
class SupervisorTests(unittest.TestCase):
    def test_invalid_delegation_cannot_execute_and_clarification_interrupts(self):
        for choice, status in (("free_sql", "error"), ("clarify", "clarify")):
            with self.subTest(choice=choice):
                trace = {}
                result = run_supervisor(AgentRuntime(lambda _: json.dumps({"decision": choice})), trace,
                                        contexts={"data": role_context("查订单量")},
                                        execute=lambda _: self.fail("unexpected tool call"))
                self.assertEqual(result["status"], status)
                self.assertEqual(trace["interrupt_required"], status == "clarify")

    def test_independent_role_defer_preserves_other_result(self):
        replies = iter(["both", "execute", "defer", "clarify"])
        runtime = AgentRuntime(lambda _: json.dumps({"decision": next(replies)}))
        trace = {}

        def execute(approved):
            self.assertEqual(approved, {"data"})
            return {"status": "partial", "answer_zh": "订单量 2312"}

        result = run_supervisor(runtime, trace, contexts={
            "data": role_context("查订单量"), "knowledge_agent": role_context("PDF 规则"),
        }, execute=execute)
        self.assertIn("2312", result["answer_zh"])
        self.assertTrue(trace["interrupt_required"])
        self.assertEqual([item["stage"] for item in runtime.attempts],
                         ["supervisor", "data", "knowledge_agent", "investigation"])


@unittest.skipUnless(HAS_GRAPH and os.getenv("SUPERVISOR_LIVE_TESTS") == "1",
                     "Requires local PostgreSQL, Apple Vision and explicit Gemini opt-in")
class SupervisorLiveTests(unittest.TestCase):
    def test_synthetic_real_gemini_and_postgres(self):
        manager, _ = local_manager()
        manager.agent.supervisor = True
        owner = token_hex(32)
        embedding = FixedEmbedding()
        manager.agent.knowledge.retriever.embedding = embedding
        writer = KnowledgeStore.local(admin=True)
        extraction = extract_pdf(PDF, owner=owner)
        receipt = approve(extraction, PDF, owner=owner, slug="supervisor-" + token_hex(10),
                          title="Synthetic order count PDF", basis="Compared with synthetic sample", corrections={})
        doc = writer.index(None, embedding, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=PDF)
        report = []
        try:
            for scenario, question, roles in (
                ("fast", "2025年1月订单量是多少？", []),
                ("data", "查询2025年1月订单量和订单商品明细数", ["data"]),
                ("pdf", "请引用 PDF 扫描页中 Order count 一行的原文", ["knowledge_agent"]),
                ("hybrid", "2025年1月订单量是多少，并引用PDF第2页扫描页中以 Order count 开头的唯一英文原文。", ["data", "knowledge_agent"]),
            ):
                if os.getenv("SUPERVISOR_LIVE_SCENARIO", scenario) != scenario:
                    continue
                with self.subTest(scenario=scenario):
                    cid = manager.create_conversation(owner)["conversation_id"]
                    answer = manager.process_message(owner, cid, question, uuid4(), today=TODAY)
                    trace = answer["trace"]
                    live = public_response(answer)
                    report.append({"scenario": scenario, "status": answer["status"], "code": answer["code"],
                                   "trace_id": trace["trace_id"], "run_id": answer.get("query_id"),
                                   "plan_id": live.trace.plan_id, "attempt_id": live.trace.attempt_id,
                                   "elapsed_ms": trace.get("elapsed_ms"),
                                   "model_attempts": trace["model_attempts"], "tool_calls": len(trace["tools"]),
                                   "orchestration": trace.get("orchestration", [])})
                    self.assertEqual(answer["status"], "ok", report[-1])
                    self.assertEqual([event["node"] for event in trace.get("orchestration", [])
                                      if event.get("decision") == "execute"], roles)
                    self.assertEqual(len(trace["tools"]), 2 if scenario in {"data", "hybrid"} else 1)
                    if scenario in {"pdf", "hybrid"}:
                        self.assertTrue(any(cite.page_number == 2 and "Order count: 48" in cite.quote
                                            for cite in live.citations))
                    if scenario == "fast":
                        self.assertEqual(live.rows[0]["value"], "2312")
                    elif scenario in {"data", "hybrid"}:
                        self.assertEqual(live.rows[0]["metrics"]["order_count"]["value"], "2312")
                    saved = manager.get_query(owner, answer["query_id"])
                    self.assertEqual(live.trace, public_response(saved, history=True).trace)
            forbidden = []

            def partial_proposal(prompt):
                if prompt.startswith("[business-plan-v1]"):
                    return json.dumps(dict(PROPOSE, metric_ids=["order_count"],
                                           pdf_question="PDF扫描页的 Order count 原文。"))
                if prompt.startswith("[knowledge-extractive-v1]"):
                    forbidden.append(True)
                    return "{}"
                if prompt.startswith("[supervisor-v1]"):
                    return json.dumps({"decision": "both"})
                if prompt.startswith("[investigation-v1]"):
                    return json.dumps({"decision": "finish"})
                return json.dumps({"decision": "execute"})

            manager.generate = partial_proposal
            other = token_hex(32)
            cid = manager.create_conversation(other)["conversation_id"]
            partial = manager.process_message(
                other, cid, "2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文。", uuid4(), today=TODAY)
            self.assertEqual(partial["status"], "partial")
            view = public_response(partial)
            self.assertEqual(view.citations, [])
            self.assertEqual(forbidden, [])
            self.assertEqual(view.rows[0]["metrics"]["order_count"]["value"], "2312")
        finally:
            writer.delete(doc["document_id"], owner=owner)
            if path := os.getenv("SUPERVISOR_LIVE_REPORT"):
                from pathlib import Path
                Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
