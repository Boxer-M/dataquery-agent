import json
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.specialists import DataAnalysisAgent, KnowledgeAgent, ToolChoice
from evaluate_business_planning import reference_connection, reference_rows
from test_business_planner_v2 import PROPOSE, QUESTION, TODAY


class SpecialistPolicyTests(unittest.TestCase):
    def test_data_agent_revalidates_route_and_rejects_other_tools(self):
        request = {"metric_id": "order_count"}
        route = {"status": "STANDARD_METRIC", "required_fields": {"dws.order_day": ["order_count"]}}
        query = Mock(return_value={"status": "ok"})
        agent = DataAnalysisAgent(Mock(return_value=route), query)
        for candidate in (ToolChoice("rag_retrieval", "order_count", "wrong_role"),
                          ToolChoice("free_sql", "order_count", "wrong_role"),
                          ToolChoice("metric_query_service", "return_record_count", "wrong_subject")):
            with patch.object(agent, "choose", return_value=candidate):
                self.assertEqual(agent.execute(request, route)["error"]["code"], "data_tool_not_authorized")
        agent.resolve.return_value = route | {"metadata_version": "changed"}
        self.assertFalse(agent.execute(request, route)["agent_call"]["executed"])
        query.assert_not_called()
        self.assertEqual(agent.resolve.call_count, 4)

    def test_knowledge_agent_enforces_formal_identity_and_rag_role(self):
        catalog = Mock(return_value={"status": "ok"})
        rag = Mock(return_value={"status": "ok"})
        agent = KnowledgeAgent(catalog, rag)
        metrics = {"order_count"}
        self.assertEqual(agent.execute("formal_definition", "unknown", known_metrics=metrics)["status"], "unsupported")
        for tool in ("rag_retrieval", "free_sql"):
            with patch.object(agent, "choose", return_value=ToolChoice(tool, "order_count", "fake")):
                result = agent.execute("formal_definition", "order_count", known_metrics=metrics)
                self.assertFalse(result["agent_call"]["executed"])
        catalog.assert_not_called()
        rag.assert_not_called()
        agent.execute("formal_definition", "order_count", known_metrics=metrics)
        agent.execute("business_rule", "退款政策", known_metrics=metrics)
        agent.execute("technical_document", "字段含义", known_metrics=metrics)
        catalog.assert_called_once_with("order_count")
        self.assertEqual([call.args for call in rag.call_args_list], [("退款政策", True), ("字段含义", False)])


@unittest.skipUnless(os.getenv("KNOWLEDGE_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class SpecialistPostgresTests(unittest.TestCase):
    def test_two_specialists_and_single_metric_fast_path(self):
        manager, owner = local_manager()
        manager.generate = Mock(return_value=json.dumps(PROPOSE))
        cid = manager.create_conversation(owner)["conversation_id"]
        answer = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
        self.assertEqual(answer["status"], "ok", answer)
        result = answer["result"]
        calls = answer["trace"]["agent_calls"]
        self.assertEqual([(call["agent"], call["tool"]) for call in calls],
                         [("data_analysis", "metric_query_service")] * 2 + [("knowledge", "catalog_exact")] * 2)
        self.assertTrue(all(call["executed"] and call["model_calls"] == 0 for call in calls))
        self.assertEqual(len(answer["trace"]["model_attempts"]), 1)
        self.assertEqual(manager.generate.call_count, 1)
        history = manager.get_query(owner, answer["query_id"])
        self.assertEqual(history["agent_calls"], calls)
        with reference_connection() as pg:
            expected = {call["request"]["metric_id"]: reference_rows(pg, call["request"])["all"]["value"]
                        for call in result["plan"]["required_tool_calls"]}
        self.assertEqual({mid: int(value["value"]) for mid, value in result["results"][0]["metrics"].items()}, expected)
        for task in result["subtasks"][2:]:
            citation = task["citation"]
            self.assertEqual(citation["quote"], manager.agent.service.metrics[task["metric_id"]]["definition"])
            self.assertEqual(citation["document_version"], manager.agent.service.catalog["catalog_version"])

        manager.generate.reset_mock()
        exact = manager.process_message(owner, cid, "item_return_rate", uuid4(), today=TODAY)
        self.assertEqual(exact["status"], "ok", exact)
        self.assertFalse(exact["trace"].get("agent_calls"))
        self.assertFalse(exact["result"]["retrieval_trace"]["query_embedding_called"])
        manager.generate.assert_not_called()

        manager.generate.return_value = json.dumps({
            "metrics": ["订单量"], "time": "2025年1月", "groups": [], "filters": [], "type": "metric", "missing": [],
        })
        single = manager.process_message(owner, cid, "2025年1月订单量是多少？", uuid4(), today=TODAY)
        self.assertEqual(single["status"], "ok", single)
        self.assertEqual(single["task"], "single")
        self.assertFalse(single["trace"].get("agent_calls"))
        self.assertEqual(single["trace"]["model_attempts"], [])
        manager.generate.assert_not_called()
        self.assertEqual(single["trace"]["routing"]["rule"], "exact_metric_explicit_window")
        self.assertEqual(int(single["result"]["query_result"]["results"][0]["value"]), expected["order_count"])
        if path := os.getenv("SPECIALIST_REPORT"):
            from pathlib import Path
            Path(path).write_text(json.dumps({
                "planner_backend": "fixed model outputs; no external LLM calls",
                "hybrid": answer, "history": history, "ods": expected, "exact_fast_path": exact, "metric_fast_path": single,
            }, default=str, ensure_ascii=False, indent=2) + "\n")
