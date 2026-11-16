"""Validate the review overlay without opening the Holdout or running a model."""
from copy import deepcopy
import unittest

from benchmark.evidence_review import REPORT, assemble
from benchmark.expansion import PUBLIC, read


class GoldenEvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.report = read(REPORT)

    def test_review_preserves_previously_approved_cases(self):
        before = read(PUBLIC)["cases"]
        after = assemble(self.report)["cases"]
        self.assertEqual(len(before), len(after))
        for original, updated in zip(before, after):
            for field in ("case_id", "semantic_group_id", "template_id", "question", "context", "split"):
                self.assertEqual(original[field], updated[field])
            if original["review_status"] == "reviewed":
                self.assertEqual(original, updated)
        self.assertEqual(len(self.report["reviews"]), 27)
        self.assertEqual(sum(case["review_status"] == "reviewed" for case in after), 35)

    def test_partial_hybrid_sql_evidence_is_not_a_full_golden_label(self):
        cases = {case["case_id"]: case for case in assemble(self.report)["cases"]}
        hybrids = [review for review in self.report["reviews"] if ":hybrid:" in review["case_id"]]
        self.assertEqual(len(hybrids), 4)
        for review in hybrids:
            expected = review["evidence"]["sql"]
            self.assertEqual(expected["snapshot"], self.report["ods_snapshot"]["id"])
            self.assertTrue(expected["calls"][0]["expected_result"])
            self.assertEqual(review["evidence"]["pdf_support"], "not_confirmed")
            self.assertFalse(any(cases[review["case_id"]]["golden"].values()))

    def test_rejects_tampered_seal_missing_decision_and_unapproved_labels(self):
        for change, issue in (
            (lambda report: report.update(holdout_manifest_digest="wrong"), "seal changed"),
            (lambda report: report["reviews"].pop(), "inventory"),
            (lambda report: next(r for r in report["reviews"] if r["decision"] == "needs_review")
             .update(golden_update={"status": "ok"}), "scoring labels disagree"),
        ):
            with self.subTest(issue=issue):
                report = deepcopy(self.report)
                change(report)
                with self.assertRaisesRegex(ValueError, issue):
                    assemble(report)

    def test_pdf_page_lines_versions_and_cleanup(self):
        pdf = self.report["pdf"]
        native, revised = pdf["active_citations_at_verification"]
        self.assertEqual((native["page_number"], native["extraction_method"]), (1, "native_text"))
        self.assertEqual((revised["page_number"], revised["line_start"], revised["line_end"]), (2, 1, 2))
        self.assertEqual(revised["source_hash"], pdf["original_sha256"])
        self.assertEqual(revised["document_version"], pdf["verified_version"])
        self.assertNotEqual(revised["document_version"], pdf["superseded_citation"]["document_version"])
        self.assertTrue(all(pdf["checks"].values()))
        self.assertEqual(self.report["gemini_calls"], 0)
