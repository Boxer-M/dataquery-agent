"""Checks for the published expansion, without opening sealed cases."""
from copy import deepcopy
import unittest

from benchmark.dataset import DATASET, migrate
from benchmark.expansion import MANIFEST, PUBLIC, REPORT, compare_rows, read, similarity_review, verify
from benchmark.scoring import scores


class GoldenExpansionTests(unittest.TestCase):
    def setUp(self):
        self.historical = read(DATASET)
        self.public = read(PUBLIC)
        self.manifest = read(MANIFEST)

    def test_release_inventory_and_old_runner(self):
        self.assertEqual(self.historical, migrate()[0])
        self.assertEqual(len(self.historical["cases"]), 101)
        self.assertTrue(all(case["split"] == "development" for case in self.historical["cases"]))
        visible = verify(self.public, self.manifest, self.historical)
        self.assertEqual(visible - 101 + len(self.manifest["cases"]), 80)
        report = read(REPORT)
        self.assertEqual(len(report["legacy_review"]), 7)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["performance_metrics"]["task_success"]["denominator"], 0)
        for case in self.public["cases"]:
            if sql := case["golden"]["sql"]:
                self.assertEqual(sql["snapshot"], report["ods_snapshot"]["id"])

    def test_sealed_inventory_rejects_cross_split_ids(self):
        leaked = deepcopy(self.manifest)
        leaked["cases"][0]["template_id"] = self.public["cases"][0]["template_id"]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            verify(self.public, leaked, self.historical)

    def test_pending_never_contributes_truth(self):
        pending = next(row for row in self.public["cases"] if row["review_status"] == "needs_review")
        pending["golden"]["status"] = "ok"
        with self.assertRaisesRegex(ValueError, "pending cases"):
            verify(self.public, self.manifest, self.historical)

    def test_same_question_masking_different_dates_flags_leakage(self):
        base = {"case_id": "old", "question": "2023年2月按渠道列基金总金额",
                "split": "development", "review_status": "reviewed"}
        variation = base | {"case_id": "new", "split": "validation",
                            "question": "2025年6月按渠道列基金总金额"}
        self.assertEqual(similarity_review([base, variation]), [["new", "old"]])

    def test_numeric_score_keeps_decimal_and_na_separate(self):
        expected = [{"bucket": "web", "value": "0.12500000", "numerator": 1, "denominator": 8}]
        observed = [{"bucket": "web", "value": "0.125", "numerator": 1, "denominator": 8}]
        wrong = [{"bucket": "web", "value": "0.125", "numerator": 2, "denominator": 16}]
        observations = [{"sql_result_accuracy": compare_rows(expected, rows)}
                        for rows in (observed, wrong, None)]
        self.assertEqual(scores(observations)["sql_result_accuracy"],
                         {"numerator": 1, "denominator": 2, "not_applicable": 1, "value": 0.5})
        huge = [{"bucket": "all", "value": "9007199254740993.01"}]
        self.assertFalse(compare_rows(huge, [{"bucket": "all", "value": "9007199254740993.02"}]))
