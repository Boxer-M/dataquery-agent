"""Offline checks for the live baseline's scope, attribution and budget."""
import unittest
from unittest.mock import Mock, patch

from benchmark.live_baseline import finalize, inventory, technical_fixture
from benchmark.live_baseline_budget import BaselineGuard, Budget, generator


class RealGeminiBaselineTests(unittest.TestCase):
    def test_technical_scope_requires_opt_in_and_matches_four_spans(self):
        scoped, excluded, _, public = inventory()
        broader, _, _, _ = inventory(authorized_technical=True)
        self.assertEqual(len(broader) - len(scoped), 8)
        self.assertEqual(sum(r["reason"] == "relevant_passage_outside_authorized_corpus" for r in excluded), 8)
        self.assertTrue(all(case["split"] in {"development", "validation"}
                            and case["review_status"] != "needs_review" for case, _, _ in broader))
        self.assertEqual([p.location for p in technical_fixture(public).passages],
                         ["lines:3-6", "lines:23-28", "lines:71-72", "lines:88-89"])

    def test_rejects_unknown_prompt_and_unauthorized_evidence_before_billing(self):
        budget = Budget("models/gemini-3.5-flash-lite")
        backend = Mock()
        blocked = []
        generate = generator(backend, budget, {("approved", "text")}, blocked)
        for prompt in ("[other] ignored",
                       '[knowledge-extractive-v1]\n{"evidence":[{"title":"approved","text":"modified"}]}'):
            with self.assertRaises(BaselineGuard):
                generate(prompt)
        backend.assert_not_called()
        self.assertEqual(len(budget.events), 0)
        self.assertEqual(len(blocked), 2)

    def test_provider_region_error_blocks_only_affected_endpoint(self):
        class RegionError(ValueError):
            code = 400

        unsuccessful = Mock(side_effect=RegionError("User location is not supported for the API use."))
        budget = Budget("models/gemini-3.5-flash-lite")
        with self.assertRaises(RegionError):
            budget.call("embedding", unsuccessful)
        with self.assertRaisesRegex(BaselineGuard, "provider_region_unavailable"):
            budget.call("embedding", unsuccessful)
        unsuccessful.assert_called_once()
        ordinary = Mock(return_value={"model": "gemini-3.5-flash-lite", "usage": {}})
        budget.call("generation", ordinary)
        ordinary.assert_called_once()
        self.assertEqual(budget.events[0]["http_status"], 400)

    def test_reservation_prevents_call_after_budget(self):
        budget = Budget("models/gemini-3.5-flash-lite")
        with patch.dict("benchmark.live_baseline_budget.LIMITS", reserved_tokens=100):
            work = Mock()
            with self.assertRaisesRegex(BaselineGuard, "budget_exhausted"):
                budget.call("generation", work)
        work.assert_not_called()

    def test_dependency_and_model_response_cohorts(self):
        parent = {"case_id": "parent"}
        child = {"case_id": "child"}
        cases = [(parent, {"id": "seed"}, "legacy"), (child, {"id": "follow", "after": "seed"}, "legacy")]
        report = {"excluded": [], "cases": [
            parent | {"scoring_disposition": "provider_region_unavailable", "task_type": "sql"},
            child | {"sql_result_accuracy": False, "task_success": False, "task_type": "multi_turn"},
        ]}
        finalize(report, cases)
        self.assertEqual(report["cases"][1]["scoring_disposition"], "upstream_provider_region_unavailable")
        self.assertEqual(report["summary"]["accuracy"]["sql_result_accuracy"]["denominator"], 0)
        self.assertEqual(report["cohorts"]["gemini_response_received"]["executed"], 0)
