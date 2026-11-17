"""Exercise production routing boundaries with fixed model and tool responses."""
from datetime import date
import json
import unittest
from unittest.mock import Mock

from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.unified_agent import UnifiedAgent


class RoutingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = Mock()
        self.knowledge.exact.return_value = {"status": "ok", "retrieval_trace": {"strategy": "catalog_exact"}}
        self.knowledge.answer.return_value = {"status": "ok", "retrieval_trace": {
            "strategy": "pgvector-cosine-exact-v1", "trace_id": "fixed"}}
        self.agent = UnifiedAgent(MetricQueryService(None), Mock(), knowledge=self.knowledge)
        self.generate = Mock(side_effect=AssertionError("unexpected model request"))
        self.today = date(2026, 11, 17)

    def test_explicit_definition_wins_over_grouping_language(self):
        for question in ("请给出 item_discount_amount 的正式定义",
                         "ordering_customer_count 的正式定义按访客还是客户计？无需计算数值"):
            with self.subTest(question=question):
                answer = self.agent.answer(question, self.generate, today=self.today)
                self.assertEqual(answer["result"]["knowledge_topic"], "metric_definition")
        self.knowledge.answer.assert_not_called()
        self.generate.assert_not_called()

    def test_timed_distribution_and_negated_definition_follow_metric_path(self):
        metric = "退款记录超明细净额数"
        for question in (f"以退货事件为时间口径，2025年10月{metric}按渠道怎么分布？",
                         f"2025年10月{metric}是多少？不要正式定义"):
            with self.subTest(question=question):
                self.generate.side_effect = None
                self.generate.return_value = json.dumps({"metrics": [metric], "time": "2025年10月",
                    "groups": ["按渠道"] if "按渠道" in question else [], "filters": [], "type": "metric", "missing": []})
                with unittest.mock.patch.object(self.agent, "run_metric_request", return_value={"status": "ok"}) as metric_path:
                    answer = self.agent.answer(question, self.generate, today=self.today)
                self.assertEqual(answer["status"], "ok")
                self.assertEqual(metric_path.call_args.args[0]["metric_id"], "return_above_item_net_count")
        self.knowledge.exact.assert_not_called()
        self.knowledge.answer.assert_not_called()

    def test_scanned_page_uses_existing_pdf_retrieval_scope(self):
        answer = self.agent.answer("扫描页修订后的原图坐标在哪里？", self.generate, today=self.today,
                                   knowledge_owner="test-owner")
        self.assertEqual(answer["result"]["knowledge_topic"], "document")
        self.assertTrue(self.knowledge.answer.call_args.kwargs["pdf_only"])
        self.assertEqual(self.knowledge.answer.call_args.kwargs["owner"], "test-owner")
        self.knowledge.exact.assert_not_called()

    def test_relative_return_horizon_retains_entity_without_dispatch(self):
        answer = self.agent.answer("商品明细在购买后14天的退货率有支持的口径吗？", self.generate,
                                   today=self.today)
        self.assertEqual(answer["status"], "clarify")
        self.assertEqual(answer["code"], "unsupported_observation_window")
        self.assertEqual(answer["match"]["confirmed"], ["item_return_rate"])
        self.assertEqual(answer["trace"]["tools"], [])
        self.generate.assert_not_called()
