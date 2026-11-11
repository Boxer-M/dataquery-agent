import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from dataquery_agent.dws import metric_query
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT, read_queries

FULL = dict(start_date="2023-01-01", end_date="2026-01-01")


class MetricRequestTests(unittest.TestCase):
    def setUp(self):
        self.service = MetricQueryService(None)

    def test_verified_catalog_is_exact(self):
        self.assertEqual(len(self.service.registry), 26)
        self.assertEqual(len(self.service.metrics), 38)

    def test_capability_errors(self):
        expected = {"made_up": "unknown_metric", "payment_attempted_order_count": "metric_unverified"}
        expected.update({key: "unsupported" for key, metric in self.service.metrics.items()
                         if metric["definition_status"] == "unsupported"})
        self.service.metrics["needs_definition_example"] = {
            "definition_status": "needs_definition", "definition": "Synthetic pending definition."
        }
        expected["needs_definition_example"] = "needs_definition"
        for metric, code in expected.items():
            with self.subTest(metric=metric):
                self.assertEqual(self.service.query(dict(FULL, metric_id=metric))["error"]["code"], code)

    def test_rejects_unsupported_axes_filters_and_injection(self):
        variants = [
            ({"group_by": ["channel", "product"]}, "unsupported_grouping"),
            ({"group_by": ["channel); DROP TABLE dws.order_day; --"]}, "unsupported_grouping"),
            ({"filters": {"channel": "x' OR 1=1 --"}}, "invalid_filter"),
            ({"filters": {"status": "cancelled"}}, "unsupported_filter"),
            ({"sql": "SELECT 1"}, "invalid_request"),
            ({"start_date": "2023-01-01T10:00:00"}, "invalid_date"),
        ]
        for extras, code in variants:
            with self.subTest(extras=extras):
                self.assertEqual(self.service.query(dict(FULL, metric_id="order_count") | extras)["error"]["code"], code)

    def test_cohort_requires_exact_cutoff_and_compatible_window(self):
        request = dict(FULL, metric_id="item_return_rate", observation_cutoff="2026-01-01")
        self.assertEqual(self.service.query(request)["error"]["code"], "database_unavailable")
        for change in [{"observation_cutoff": "2026-02-01"}, {"end_date": "2026-01-02"}]:
            self.assertEqual(self.service.query(request | change)["error"]["code"], "unsupported_cutoff")
        self.assertEqual(self.service.query(request | {"metric_id": "return_record_amount"})["error"]["code"],
                         "unsupported_cutoff")

    def test_admin_dsn_is_rejected_before_connecting(self):
        self.service.dsn = "dbname=sqlshed user=dataquery_admin"
        with patch("dataquery_agent.metric_query_service.psycopg.connect") as connect:
            response = self.service.query(dict(FULL, metric_id="order_count"))
            self.assertEqual(response["error"]["code"], "unsafe_database_role")
            connect.assert_not_called()

    def test_timeout_and_connection_errors_are_structured(self):
        for error, code in [(psycopg.errors.QueryCanceled("sensitive"), "query_timeout"),
                            (psycopg.OperationalError("sensitive"), "database_unavailable")]:
            with patch.object(self.service, "execute", side_effect=error):
                response = self.service.query(dict(FULL, metric_id="order_count"))
                self.assertEqual(response["error"]["code"], code)
                self.assertNotIn("sensitive", json.dumps(response))
                self.assertEqual(len(response["trace_id"]), 32)


@unittest.skipUnless(os.environ.get("METRIC_QUERY_POSTGRES_TESTS") == "1", "Requires local warehouse and service login.")
class MetricPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = dotenv_values(ROOT / ".env.metric-query")
        cls.service = MetricQueryService(cfg["METRIC_QUERY_DSN"])
        cls.reference = read_queries(ROOT / "sql/warehouse/dws_metrics/ods.sql")

    def query(self, mid, **extra):
        request = dict(FULL, metric_id=mid) | extra
        if mid == "item_return_rate":
            request["observation_cutoff"] = "2026-01-01"
        response = self.service.query(request)
        self.assertEqual(response["status"], "ok", response.get("error"))
        return response["results"]

    def test_all_26_against_independent_ods(self):
        admin = dotenv_values(ROOT / ".env")
        with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed",
                             user=admin["POSTGRES_USER"], password=admin["POSTGRES_PASSWORD"],
                             row_factory=dict_row) as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '15s'")
            for mid in self.service.registry:
                required = {"channel_order_share": "channel", "category_sales_share": "category"}.get(mid)
                for start, end, axis, month in [
                    ("2023-01-01", "2026-01-01", required, False),
                    ("2025-01-01", "2025-02-01", required or "channel", True),
                    ("2022-01-01", "2022-02-01", required, False),
                ]:
                    with self.subTest(metric=mid, start=start, axis=axis):
                        groups = ["month"] if month else []
                        if axis:
                            groups.append(axis)
                        filters = {"channel": "web"} if month else {}
                        actual = self.query(mid, start_date=start, end_date=end, group_by=groups, filters=filters)
                        reference = metric_query(pg, self.reference[mid], axis if not required else None)
                        expected = pg.execute(reference, dict(start=start, end=end, as_of=self.service.cutoff,
                                                            channel=filters.get("channel"), category=None)).fetchall()
                        if month:
                            self.assertTrue(actual)
                            self.assertTrue(all(row["month"] == "2025-01" for row in actual))
                        for row in actual:
                            for key in ("month", "start_date", "end_date"):
                                row.pop(key, None)
                            for key in ("value", "numerator", "denominator"):
                                if isinstance(row.get(key), str):
                                    row[key] = Decimal(row[key])
                        self.assertEqual(sorted(actual, key=lambda row: str(row["bucket"])),
                                         sorted(expected, key=lambda row: str(row["bucket"])))

    def test_exact_money_ratio_and_empty(self):
        self.assertEqual(self.query("item_net_sales_amount")[0]["value"], "9024059.76")
        ratio = self.query("item_return_rate")[0]
        self.assertEqual((ratio["value"], ratio["numerator"], ratio["denominator"]),
                         ("0.05581395", "12000", "215000"))
        empty = self.query("item_return_rate", start_date="2022-01-01", end_date="2022-02-01")[0]
        self.assertEqual((empty["value"], empty["numerator"], empty["denominator"]), (None, "0", "0"))

    def test_months_recompute_and_preserve_partial_bounds(self):
        rows = self.query("customer_repeat_order_rate", start_date="2025-01-20",
                          end_date="2025-03-04", group_by=["month"])
        self.assertEqual([row["month"] for row in rows], ["2025-01", "2025-02", "2025-03"])
        for row in rows:
            expected = self.query("customer_repeat_order_rate", start_date=row["start_date"],
                                  end_date=row["end_date"])[0]
            self.assertEqual(row["value"], expected["value"])

    def test_acl_and_result_limit(self):
        with psycopg.connect(self.service.dsn, autocommit=True) as pg:
            self.assertEqual(pg.execute(
                "SELECT current_user, current_setting('default_transaction_read_only'), "
                "has_schema_privilege('ods','USAGE'), has_schema_privilege('ads','USAGE'), "
                "has_table_privilege('dws.order_day','INSERT,UPDATE,DELETE'), "
                "has_database_privilege(current_database(),'CREATE,TEMP')"
            ).fetchone(), ("sqlshed_metric_service", "on", False, False, False, False))
            pg.execute("SET default_transaction_read_only=off")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                pg.execute("UPDATE dws.order_day SET order_count=0 WHERE false")
        response = self.service.query(dict(FULL, metric_id="ordered_product_count", group_by=["product"]))
        self.assertEqual(response["error"]["code"], "result_limit")
        self.assertEqual(response["results"], [])

    def test_real_statement_timeout(self):
        template = "SELECT {bucket} AS bucket, 1 AS value FROM pg_sleep(1)"
        with patch.dict(self.service.templates, {"order_count": template}), patch(
            "dataquery_agent.metric_query_service.TIMEOUT_MS", 50
        ):
            response = self.service.query(dict(FULL, metric_id="order_count"))
        self.assertEqual(response["error"]["code"], "query_timeout")
