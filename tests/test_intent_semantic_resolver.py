import json
import os
import unittest
from datetime import date
from unittest.mock import Mock

from dotenv import dotenv_values

from dataquery_agent.intent_semantic_resolver import SemanticResolver, gemini_generator
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT

FIXTURE = json.loads((ROOT / "tests/fixtures/intent_business_v1.json").read_text())
TODAY = date.fromisoformat(FIXTURE["today"])
BY_ID = {case["id"]: case for case in FIXTURE["cases"]}


def fixture_output(case):
    if "raw" in case:
        return case["raw"]
    intent = {"metrics": [], "time": None, "groups": [], "filters": [],
              "type": "metric", "missing": []}
    intent.update(case["intent"])
    return json.dumps(intent, ensure_ascii=False)


class IntentBusinessTests(unittest.TestCase):
    def setUp(self):
        self.service = MetricQueryService(None)
        self.service.query = Mock()
        self.resolver = SemanticResolver(self.service)

    def test_versioned_business_expectations(self):
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["id"]):
                self.service.query.reset_mock()
                self.service.query.return_value = {
                    "status": "error",
                    "error": {"code": "query_timeout"},
                } if case.get("tool_error") else {"status": "ok", "results": []}
                answer = self.resolver.answer(
                    case["question"], lambda _: fixture_output(case), today=TODAY)
                if case["status"] == "ready":
                    self.assertEqual(answer["status"], "ok", answer)
                    self.assertEqual(answer["request"], case["request"])
                    self.service.query.assert_called_once_with(case["request"])
                elif case.get("tool_error"):
                    self.assertEqual(answer["query_result"]["error"]["code"], case["code"])
                    self.service.query.assert_called_once()
                else:
                    self.assertEqual((answer["status"], answer["code"]),
                                     (case["status"], case["code"]), answer)
                    self.service.query.assert_not_called()

    def test_sales_clarification_contains_real_distinct_definitions(self):
        case = BY_ID["ambiguous_sales"]
        answer = self.resolver.answer(case["question"], lambda _: fixture_output(case), today=TODAY)
        self.assertEqual({item["metric_id"] for item in answer["choices"]},
                         {"item_net_sales_amount", "item_gross_sales_amount", "captured_payment_amount"})
        self.assertEqual(len({item["definition"] for item in answer["choices"]}), 3)

    def test_model_and_tool_exceptions_are_safe(self):
        case = BY_ID["catalog_name"]
        model = Mock(side_effect=RuntimeError("secret model error"))
        result = self.resolver.answer(case["question"], model, today=TODAY)
        self.assertEqual(result["code"], "model_unavailable")
        self.assertNotIn("secret", str(result))
        self.service.query.assert_not_called()
        self.service.query.side_effect = RuntimeError("secret database error")
        result = self.resolver.answer(case["question"], lambda _: fixture_output(case), today=TODAY)
        self.assertEqual(result["code"], "query_tool_unavailable")
        self.assertNotIn("secret", str(result))

    def test_partial_dates_and_omitted_constraints_do_not_broaden_query(self):
        for question, time, code in [
            ("2025年1月15日的订单量", "2025年1月", "unsupported_time"),
            ("2025年第一季度订单量", "2025年", "unsupported_time"),
            ("2025年1月网页端订单量", "2025年1月", "unsupported_filter"),
            ("2025年1月订单量按地区统计", "2025年1月", "unsupported_grouping"),
            ("2025年1月不含取消订单的订单量", "2025年1月", "unsupported_filter"),
            ("2025年1月已支付订单量", "2025年1月", "unsupported_filter"),
            ("2025年1月之前的订单量", "2025年1月", "unsupported_time"),
        ]:
            with self.subTest(question=question):
                case = {"intent": {"metrics": ["订单量"], "time": time}}
                result = self.resolver.answer(question, lambda _: fixture_output(case), today=TODAY)
                self.assertEqual(result["code"], code, result)
                self.service.query.assert_not_called()


@unittest.skipUnless(os.environ.get("METRIC_QUERY_POSTGRES_TESTS") == "1", "Requires local PostgreSQL.")
class IntentPostgresTests(unittest.TestCase):
    def test_real_metric_results_with_recorded_parser_inputs(self):
        service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query")["METRIC_QUERY_DSN"])
        resolver = SemanticResolver(service)
        for key in ("catalog_name", "channel", "cutoff"):
            case = BY_ID[key]
            with self.subTest(case=key):
                answer = resolver.answer(case["question"], lambda _: fixture_output(case), today=TODAY)
                self.assertEqual(answer["status"], "ok", answer)
                actual = answer["query_result"]
                reference = service.query(case["request"])
                self.assertEqual(actual["results"], reference["results"])
                self.assertTrue(actual["results"])
                self.assertTrue(actual["trace_id"])
                if key == "cutoff":
                    self.assertIn("numerator", actual["results"][0])
                    self.assertIn("denominator", actual["results"][0])


@unittest.skipUnless(os.environ.get("INTENT_GEMINI_TESTS") == "1", "Requires configured Gemini and PostgreSQL.")
class IntentGeminiTests(unittest.TestCase):
    def test_live_gemini_to_postgres_and_clarification(self):
        service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query")["METRIC_QUERY_DSN"])
        resolver = SemanticResolver(service)
        generate = gemini_generator(dotenv_values(ROOT / ".env"))
        for key in ("catalog_name", "ambiguous_sales", "channel", "cutoff"):
            case = BY_ID[key]
            with self.subTest(case=key):
                actual = resolver.answer(case["question"], generate, today=TODAY)
                if case["status"] == "ready":
                    self.assertEqual(actual["status"], "ok", actual)
                    self.assertEqual(actual["request"], case["request"])
                    expected = service.query(case["request"])
                    self.assertEqual(actual["query_result"]["results"], expected["results"])
                    self.assertTrue(actual["query_result"]["trace_id"])
                else:
                    self.assertEqual((actual["status"], actual["code"]), (case["status"], case["code"]))
