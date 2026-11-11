import json
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from dataquery_agent.business_query_planner import BusinessQueryPlanner
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT

FIXTURE = json.loads((ROOT / "tests/fixtures/business_planning_v1.json").read_text())
TODAY = date.fromisoformat(FIXTURE["today"])
CASES = {case["id"]: case for case in FIXTURE["cases"]}
PROPOSAL = {
    "analysis_type": "summary", "metric_ids": [], "time_windows": [], "group_by": [],
    "filters": [], "comparison_type": None, "sort_rule": None, "limit": None,
    "conditions": [], "unresolved_business_rules": [],
}


def proposal_output(case):
    return case.get("raw", json.dumps(PROPOSAL | case.get("proposal", {}), ensure_ascii=False))


def stub_result(request, scenario=None):
    mid = request["metric_id"]
    if scenario == "partial" and mid == "captured_payment_amount":
        return {"status": "error", "error": {"code": "query_timeout"}, "trace_id": "fixed-error"}
    if scenario == "empty":
        rows = []
    elif scenario == "zero":
        rows = [{"bucket": "all", "value": "0" if request["start_date"] == "2025-01-01" else "15"}]
    elif scenario == "zero_ratio":
        rows = [{"bucket": "web", "value": None, "numerator": "0", "denominator": "0"}]
    elif mid == "payment_failed_attempt_rate":
        rows = [
            {"bucket": "web", "value": "0.10000000", "numerator": 10, "denominator": 100},
            {"bucket": "mobile", "value": "0.20000000", "numerator": 4, "denominator": 20},
        ]
    elif request["group_by"]:
        values = ("900.10", "1400.25", "200.00") if mid == "captured_payment_amount" else ("600", "700", "200")
        rows = [{"bucket": bucket, "value": value} for bucket, value in zip(("web", "mobile", "phone"), values)]
    else:
        values = {"order_count": ("100", "120"), "item_net_sales_amount": ("100.25", "90.20"),
                  "captured_payment_amount": ("144.01", "155.02")}
        rows = [{"bucket": "all", "value": values[mid][request["start_date"] == "2025-02-01"]}]
    return {**request, "status": "ok", "results": rows, "trace_id": "fixed-query", "sources": ["fixture"]}


def expected_plan(case, result):
    expected = case["expected"]
    if "type" not in expected:
        return result.get("code") == expected["code"] and "plan" not in result
    plan = result.get("plan", {})
    windows = [[window["start_date"], window["end_date"]] for window in plan.get("time_windows", [])]
    return (
        plan.get("analysis_type") == expected["type"] and plan.get("metric_ids") == expected["metrics"]
        and windows == expected["windows"] and plan.get("group_by") == expected["groups"]
        and plan.get("limit") == expected.get("limit")
        and len(plan.get("required_tool_calls", [])) == expected["calls"]
    )


def expected_requests(case):
    expected = case["expected"]
    return [
        {"metric_id": mid, "start_date": start, "end_date": end, "group_by": expected["groups"],
         "filters": expected.get("filters", {})}
        for start, end in expected.get("windows", [])
        for mid in expected["metrics"]
    ]


class BusinessPlannerTests(unittest.TestCase):
    def setUp(self):
        self.service = MetricQueryService(None)
        self.service.query = Mock()
        self.planner = BusinessQueryPlanner(self.service)

    def test_versioned_business_suite(self):
        for case in CASES.values():
            with self.subTest(case=case["id"]):
                self.service.query.reset_mock()
                self.service.query.side_effect = lambda request: stub_result(request, case.get("tool"))
                result = self.planner.answer(case["question"], lambda _: proposal_output(case), today=TODAY)
                expected = case["expected"]
                self.assertEqual(result["status"], expected["status"], result)
                self.assertTrue(expected_plan(case, result), result)
                self.assertEqual([call.args[0] for call in self.service.query.call_args_list],
                                 expected_requests(case))
                self.check_operation(case, result)

    def check_operation(self, case, result):
        operation = case["expected"].get("operation")
        rows = result.get("results", [])
        if operation in {"empty", "no_rankable"}:
            self.assertEqual(rows, [])
        elif operation in {"top", "filter_top"}:
            expected = ["mobile", "web"]
            self.assertEqual([row["bucket"] for row in rows], expected)
            if case["id"] == "failure_rate":
                self.assertEqual(rows[0]["metrics"]["payment_failed_attempt_rate"]["denominator"], "20")
        elif operation == "zero_base":
            cell = rows[0]["metrics"]["order_count"]
            self.assertEqual((cell["change"], cell["change_pct"], cell["change_pct_reason"]),
                             ("15", None, "zero_baseline"))
        elif operation == "change":
            mid = case["expected"]["metrics"][0]
            cell = rows[0]["metrics"][mid]
            if mid == "order_count":
                self.assertEqual((cell["change"], cell["change_pct"]), ("20", "20.00000000"))
            else:
                self.assertEqual((cell["change"], cell["change_pct"]), ("-10.05", "-10.02493766"))
        elif operation == "preserve_success":
            self.assertEqual(result["tools"][1]["error_code"], "query_timeout")
            self.assertIsNone(rows[0]["metrics"]["captured_payment_amount"])
            self.assertEqual(rows[0]["metrics"]["order_count"]["value"], "100")
        elif operation == "align":
            self.assertEqual(rows[0]["metrics"]["captured_payment_amount"]["value"], "144.01")
            self.assertIn("payments.paid_at", result["answer_zh"])

    def test_plan_budget_preserves_completed_step(self):
        case = CASES["multi"]
        self.service.query.side_effect = stub_result
        with patch("dataquery_agent.business_query_planner.monotonic", side_effect=[0, 0, 0, 0, 1, 31, 31]):
            result = self.planner.answer(case["question"], lambda _: proposal_output(case), today=TODAY)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["tools"][1]["error_code"], "plan_timeout")
        self.service.query.assert_called_once()

    def test_model_cannot_invent_metric_or_change_sort(self):
        for key, update, error in [
            ("multi", {"metric_ids": ["made_up"]}, "metric_mismatch"),
            ("channel_top", {"sort_rule": {"metric_id": "order_count", "direction": "asc"}}, "unclear_ranking"),
        ]:
            case = CASES[key]
            raw = json.loads(proposal_output(case)) | update
            result = self.planner.answer(case["question"], lambda _: json.dumps(raw), today=TODAY)
            self.assertEqual(result["code"], error)
        self.service.query.assert_not_called()

    def test_invalid_ratio_fails_step(self):
        case = CASES["failure_rate"]
        self.service.query.side_effect = lambda request: {
            **request, "status": "ok",
            "results": [{"bucket": "web", "value": "0.25", "numerator": "0", "denominator": "0"}],
        }
        result = self.planner.answer(case["question"], lambda _: proposal_output(case), today=TODAY)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["tools"][0]["error_code"], "invalid_tool_response")

    def test_cross_filter_without_required_metric_preserves_success(self):
        case = CASES["cross"]

        def tool(request):
            if request["metric_id"] == "order_count":
                return {"status": "error", "error": {"code": "query_timeout"}}
            return stub_result(request)

        self.service.query.side_effect = tool
        result = self.planner.answer(case["question"], lambda _: proposal_output(case), today=TODAY)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["notice"], "postprocessing_skipped")
        self.assertEqual(len(result["results"]), 3)
        self.assertTrue(all(row["metrics"]["order_count"] is None for row in result["results"]))

    def test_ratio_plan_requires_components_before_call(self):
        case = CASES["failure_rate"]
        self.service.templates["payment_failed_attempt_rate"] = "SELECT 0 AS value"
        result = self.planner.answer(case["question"], lambda _: proposal_output(case), today=TODAY)
        self.assertEqual(result["code"], "ratio_components_missing")
        self.service.query.assert_not_called()
