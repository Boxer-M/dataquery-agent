import csv
import tempfile
import unittest
from pathlib import Path

from dataquery_agent.data_audit import FileAuditConfig, audit_csv


class AuditCsvTests(unittest.TestCase):
    def test_reports_nulls_duplicate_key_invalid_date_and_invalid_money(self) -> None:
        rows = [
            {"id": "a", "event_at": "2020-01-01 00:00:00", "amount": "10.25"},
            {"id": "a", "event_at": "invalid", "amount": "not-a-number"},
            {"id": "", "event_at": "", "amount": "-1.00"},
            {"id": "b", "event_at": "2020-01-03", "amount": ""},
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            csv_path = Path(temporary_directory) / "sample.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as file_handle:
                writer = csv.DictWriter(
                    file_handle, fieldnames=["id", "event_at", "amount"]
                )
                writer.writeheader()
                writer.writerows(rows)

            report, _ = audit_csv(
                csv_path,
                FileAuditConfig(
                    candidate_keys=(("id",),),
                    date_fields=("event_at",),
                    money_fields=("amount",),
                ),
            )

        self.assertEqual(report["row_count"], 4)
        self.assertEqual(report["null_counts"], {"id": 1, "event_at": 1, "amount": 1})
        self.assertEqual(report["candidate_keys"]["id"]["duplicate_rows"], 1)
        self.assertEqual(report["candidate_keys"]["id"]["rows_with_null_key"], 1)
        self.assertEqual(report["date_fields"]["event_at"]["parse_error_count"], 1)
        self.assertEqual(report["date_fields"]["event_at"]["null_count"], 1)
        self.assertEqual(report["date_fields"]["event_at"]["minimum"], "2020-01-01 00:00:00")
        self.assertEqual(report["date_fields"]["event_at"]["maximum"], "2020-01-03 00:00:00")
        self.assertEqual(report["money_fields"]["amount"]["parse_error_count"], 1)
        self.assertEqual(report["money_fields"]["amount"]["null_count"], 1)
        self.assertEqual(report["money_fields"]["amount"]["negative_count"], 1)
        self.assertEqual(report["money_fields"]["amount"]["minimum"], "-1.00")
        self.assertEqual(report["money_fields"]["amount"]["maximum"], "10.25")


if __name__ == "__main__":
    unittest.main()
