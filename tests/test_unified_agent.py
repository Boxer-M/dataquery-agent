import json
import unittest
from datetime import date
from unittest.mock import Mock

from dataquery_agent.agent_prompts import PROMPT_VERSIONS
from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.unified_agent import UnifiedAgent
from test_business_query_planner import CASES as BUSINESS, expected_requests, proposal_output, stub_result
from test_metadata_capability import fixture_snapshot

FIXTURE = json.loads((ROOT / "tests/fixtures/unified_agent_v1.json").read_text())
TODAY = date.fromisoformat(FIXTURE["today"])
CASES = {case["id"]: case for case in FIXTURE["cases"]}
JAN = {"start_date": "2025-01-01", "end_date": "2025-02-01"}


def question(case):
    return BUSINESS[case["business"]]["question"] if "business" in case else case["question"]


def expected_calls(case):
    if "business" in case:
        return expected_requests(BUSINESS[case["business"]])
    if case["status"] == "ok" or case.get("tool_error"):
        request = {"metric_id": case["metric"], **JAN}
        if "filter" in case:
            request["filters"] = case["filter"]
        if case["metric"] == "item_return_rate":
            request["observation_cutoff"] = "2026-01-01"
        return [request]
    return []


def fixed_generate(case, prompt):
    if "business" in case:
        return proposal_output(BUSINESS[case["business"]])
    if prompt.startswith(f"[{PROMPT_VERSIONS['resolution']}]"):
        return json.dumps(case["match"], ensure_ascii=False)
    if "raw" in case:
        return case["raw"]
    intent = {"metrics": [], "time": None, "groups": [], "filters": [], "type": "metric", "missing": []}
    return json.dumps(intent | case["intent"], ensure_ascii=False)


class UnifiedAgentTests(unittest.TestCase):
    def setUp(self):
        self.service = MetricQueryService(None)
        self.registry = CapabilityRegistry(fixture_snapshot())
        self.capability = CapabilityResolver(self.registry)
        self.agent = UnifiedAgent(self.service, self.capability)
        self.service.query = Mock()

    def fake_query(self, case, request):
        if case.get("tool_error"):
            return {"status": "error", "error": {"code": "query_timeout"}, "trace_id": "fixture-error"}
        if "business" in case:
            result = stub_result(request, BUSINESS[case["business"]].get("tool"))
        else:
            row = {"bucket": "all", "value": "123"}
            if request["metric_id"] in {"payment_attempt_success_rate", "item_return_rate"}:
                row.update(value="0.50000000", numerator="1", denominator="2")
            result = {**request, "status": "ok", "results": [row], "trace_id": "fixture-query"}
        return dict(result, sources=self.registry.standard[request["metric_id"]]["sources"],
                    definition=self.service.metrics[request["metric_id"]]["definition"])

    def test_versioned_business_cases(self):
        for case in CASES.values():
            with self.subTest(case=case["id"]):
                self.service.query.reset_mock()
                self.service.query.side_effect = lambda request: self.fake_query(case, request)
                generate = Mock(side_effect=lambda prompt: fixed_generate(case, prompt))
                result = self.agent.answer(question(case), generate, today=TODAY)
                self.assertEqual((result["task"], result["status"]), (case["task"], case["status"]), result)
                self.assertEqual(result["code"], case.get("code"), result)
                self.assertEqual([call.args[0] for call in self.service.query.call_args_list],
                                 expected_calls(case), result)
                self.assertEqual(len(result["trace"]["tools"]), len(expected_calls(case)))
                if "source" in case:
                    self.assertEqual(result["routes"][0]["selected_source"], case["source"])
                    self.assertEqual(result["routes"][0]["execution_policy"], "plan_only")
                if case["status"] == "ok" and "metric" in case:
                    self.assertIn(case["metric"], result["match"]["candidate_ids"])
                    self.assertEqual(result["match"]["confirmed"], [case["metric"]])
                if case["task"] == "blocked":
                    generate.assert_not_called()
                for tool in result["trace"]["tools"]:
                    self.assertIn(tool["capability_trace_id"], [route["trace_id"] for route in result["routes"]])
                    if tool["status"] == "ok":
                        self.assertEqual(set(tool["sources"]),
                                         set(self.registry.standard[tool["request"]["metric_id"]]["sources"]))

    def test_retry_keeps_all_attempts_and_token_usage(self):
        case = CASES["order"]
        self.service.query.side_effect = lambda request: self.fake_query(case, request)
        generate = Mock(side_effect=[TimeoutError("private"),
                                    {"text": fixed_generate(case, ""), "usage": {"prompt": 12, "total": 26}}])
        result = self.agent.answer(question(case), generate, today=TODAY)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual([item["status"] for item in result["trace"]["model_attempts"]], ["error", "received"])
        self.assertEqual(result["trace"]["model_attempts"][1]["usage"]["total"], 26)
        self.assertNotIn("private", str(result))

    def test_business_preflight_blocks_all_calls_if_one_route_denied(self):
        case = CASES["multi"]
        registry = self.registry
        registry.snapshot.get("dws.payment_day")["columns"]["attempt_amount"]["metric_readable"] = False
        result = self.agent.answer(question(case), lambda prompt: fixed_generate(case, prompt), today=TODAY)
        self.assertEqual(result["code"], "access_denied")
        self.assertEqual(len(result["routes"]), 2)
        self.service.query.assert_not_called()

    def test_source_mismatch_does_not_publish_result(self):
        case = CASES["order"]
        self.service.query.side_effect = lambda request: dict(
            self.fake_query(case, request), sources=["ods.orders"])
        result = self.agent.answer(question(case), lambda prompt: fixed_generate(case, prompt), today=TODAY)
        self.assertEqual(result["code"], "tool_provenance_mismatch")
        self.assertNotIn("query_result", result["result"])

    def test_candidate_model_cannot_confirm_without_independent_evidence(self):
        candidates = self.agent.candidates
        evidence = "订单情况"
        shortlist = candidates.shortlist(evidence)
        self.assertIn("order_count", {item["metric_id"] for item in shortlist})
        proposed, binding = candidates.validate(json.dumps({"metric_ids": ["order_count"],
                                                            "evidence": evidence}), evidence, shortlist)
        self.assertEqual(proposed, ["order_count"])
        self.assertIsNone(binding)
