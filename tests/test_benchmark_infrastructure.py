from copy import deepcopy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from benchmark import adapters
from benchmark.dataset import DATASET, digest, migrate, validate
from benchmark.runner import run
from benchmark.scoring import scores, telemetry


class BenchmarkInfrastructureTests(unittest.TestCase):
    def test_migration_keeps_source_and_counts(self):
        data, counts = migrate()
        self.assertEqual(data, json.loads(DATASET.read_text()))
        self.assertEqual([counts[key] for key in ("question_records", "unique_normalized_texts",
                         "duplicate_text_groups", "cross_file_duplicate_groups")], [101, 79, 15, 12])
        self.assertEqual({row["split"] for row in data["cases"]}, {"development"})
        cases = {row["case_id"]: row for row in data["cases"]}
        a, b, c = (cases[key] for key in ("agent_e2e_v1:single", "agent_e2e_v1:alias", "intent_business_v1:synonym"))
        self.assertEqual(a["semantic_group_id"], b["semantic_group_id"])
        self.assertNotEqual(a["semantic_group_id"], c["semantic_group_id"])
        self.assertEqual(a["template_id"], c["template_id"])
        self.assertEqual(c["source"]["original_id"], "synonym")

    def test_rejects_exact_semantic_and_template_leakage(self):
        data, _ = migrate()
        cases = {r["case_id"]: r for r in data["cases"]}
        for id_ in ("unified_agent_v1:order", "agent_e2e_v1:alias", "intent_business_v1:synonym"):
            with self.subTest(id=id_):
                source = cases["agent_e2e_v1:single"]
                other = deepcopy(cases[id_])
                other.update(split="holdout", review_status="reviewed")
                with self.assertRaisesRegex(ValueError, "cross splits"):
                    validate({"version": data["version"], "cases": [source, other]})

    def test_scoring_denominators_and_missing_values(self):
        rows = [{"intent_accuracy": True, "entities": [["a", "b"], ["a", "c"]],
                 "retrieval": [["a", "b"], ["x", "a", "a", "b"]], "latency_ms": 5, "model_calls": 0},
                {"intent_accuracy": False, "entities": [["c"], []], "retrieval": [["c"], []], "latency_ms": 100},
                {"retrieval": [[], ["x"]], "citation_validity": True}]
        actual = scores(rows, k=2)
        self.assertEqual(actual["intent_accuracy"]["not_applicable"], 1)
        self.assertEqual(actual["entity_precision"]["value"], .5)
        self.assertEqual(actual["entity_recall"]["value"], 1/3)
        self.assertEqual(actual["entity_f1"]["value"], .4)
        self.assertEqual(actual["retrieval_recall_at_2"]["value"], 1/3)
        self.assertEqual(actual["retrieval_mrr_at_2"]["value"], .25)
        self.assertIsNone(actual["citation_semantic_support"]["value"])
        self.assertEqual(telemetry(rows)["latency_ms"]["p95"], 100)
        self.assertEqual(telemetry(rows)["model_calls"]["value"], 0)
        self.assertEqual(telemetry(rows)["model_calls"]["not_applicable"], 2)

    def test_default_fixed_and_archive_replay_never_call_model(self):
        with patch("benchmark.adapters.live_run", side_effect=AssertionError("external call")):
            fixed_a, fixed_b = run("fixed"), run("fixed")
            replay = run("replay")
        self.assertEqual(fixed_a["metrics"], fixed_b["metrics"])
        self.assertEqual(fixed_a["comparison_key"], fixed_b["comparison_key"])
        self.assertEqual(replay["number_of_cases"], 13)
        self.assertEqual(replay["telemetry"]["model_calls"]["value"], 0)
        self.assertEqual(replay["identity"]["model_config"]["captured_model_attempts"], 8)
        self.assertIsNone(replay["telemetry"]["latency_ms"]["p95"])
        self.assertNotEqual(fixed_a["comparison_key"], replay["comparison_key"])

    def test_model_and_snapshot_are_part_of_identity(self):
        marks = [
            ("model-a", "database-a"), ("model-b", "database-a"), ("model-a", "database-b"),
        ]
        with patch("benchmark.runner.execute", side_effect=[
            ([], {"id": snap}, {"configured_model": model}) for model, snap in marks
        ]):
            keys = {run("gemini")["comparison_key"] for _ in marks}
        self.assertEqual(len(keys), 3)
        self.assertEqual(adapters.trace_fields({"model_attempts": [{"usage": {"total": 1}}]},
                                               mode="replay", elapsed=None)["model_calls"], 0)

    def test_live_modes_require_explicit_opt_in(self):
        for mode in ("gemini", "browser"):
            result = subprocess.run([sys.executable, "-m", "benchmark.runner", "--mode", mode],
                                    capture_output=True, text=True, cwd=DATASET.parent.parent)
            self.assertEqual(result.returncode, 2)
            self.assertIn("require --allow-live-model", result.stderr)
