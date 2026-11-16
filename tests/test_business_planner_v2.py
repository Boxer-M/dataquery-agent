import json
import os
import unittest
from datetime import date
from uuid import uuid4
from unittest.mock import Mock, patch

from dataquery_agent.business_query_planner import BusinessQueryPlanner
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.intent_semantic_resolver import ALIASES
from dataquery_agent.metric_entity import MetricEntityResolver
from dataquery_agent.metric_query_service import MetricQueryService
from evaluate_business_planning import reference_connection, reference_rows
from test_business_query_planner import PROPOSAL

TODAY = date(2026, 11, 16)
IDS = ["order_count", "order_item_count"]
QUESTION = "查询2025年1月订单量和订单商品明细数，并给出正式定义"
PROPOSE = PROPOSAL | {"metric_ids": IDS, "time_windows": ["2025年1月"]}


class PlannerV2Tests(unittest.TestCase):
    def test_multiple_ambiguous_and_unknown(self):
        service = MetricQueryService(None)
        resolver = MetricEntityResolver(service.metrics, ALIASES)
        self.assertEqual(resolver.resolve("order_count 和订单商品明细数")["status"], "multiple")
        self.assertEqual(resolver.resolve("订单量和退货率")["status"], "ambiguous")
        self.assertEqual(resolver.resolve("x_order_count")["status"], "not_found")
        planner = BusinessQueryPlanner(service)
        for question, code in (
            ("2025年1月订单量和x_order_count", "unknown_metric"),
            ("2025年1月订单量和订单商品明细数按支付方式", "unsupported_grouping"),
        ):
            proposal = PROPOSE | {"group_by": ["按支付方式"]} if "支付方式" in question else PROPOSE
            result = planner.prepare(question, lambda _: json.dumps(proposal), TODAY)
            self.assertEqual(result["code"], code, result)


@unittest.skipUnless(os.getenv("KNOWLEDGE_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class PlannerV2PostgresTests(unittest.TestCase):
    def test_joint_plan_and_partial_result(self):
        manager, owner = local_manager()
        model = Mock(return_value=json.dumps(PROPOSE))
        manager.generate = model
        store = manager.agent.knowledge.retriever.store
        cid = manager.create_conversation(owner)["conversation_id"]
        with patch.object(manager.agent.knowledge.retriever.embedding, "embed",
                          side_effect=AssertionError("unexpected query embedding")) as embed:
            answer = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
            self.assertEqual(answer["status"], "ok", answer)
            result = answer["result"]
            self.assertEqual(result["metric_resolution"]["status"], "multiple")
            self.assertEqual([task["status"] for task in result["subtasks"]], ["ok"] * 4)
            self.assertEqual(set(task["metric_id"] for task in result["subtasks"]), set(IDS))
            self.assertTrue(result["plan"]["include_catalog_definitions"])
            history = manager.get_query(owner, answer["query_id"])
            self.assertEqual(history["subtasks"], result["subtasks"])
            self.assertFalse(history["knowledge"]["query_embedding_called"])
            with reference_connection() as pg:
                expected = [reference_rows(pg, call["request"])["all"]["value"]
                            for call in result["plan"]["required_tool_calls"]]
            self.assertEqual([int(result["results"][0]["metrics"][mid]["value"]) for mid in IDS], expected)
            with patch.object(store, "catalog_chunks", return_value=[]):
                partial = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
            self.assertEqual(partial["status"], "partial")
            self.assertEqual(partial["result"]["knowledge_result"]["citations"], [])
            self.assertEqual([task["status"] for task in partial["result"]["subtasks"]],
                             ["ok", "ok", "unavailable", "unavailable"])
            embed.assert_not_called()
        if path := os.getenv("PLANNER_V2_REPORT"):
            from pathlib import Path
            Path(path).write_text(json.dumps({"question": QUESTION, "ods_values": dict(zip(IDS, expected)),
                                              "result": result["results"], "subtasks": history["subtasks"],
                                              "trace": answer["trace"], "partial": partial["result"]["subtasks"],
                                              "query_embedding_calls": embed.call_count}, default=str,
                                             ensure_ascii=False, indent=2) + "\n")
