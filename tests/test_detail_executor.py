import json
import os
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.detail_executor import DetailQueryExecutor, validated_rows
from dataquery_agent.detail_templates import DETAIL_ROLE, DetailTemplateRegistry
from dataquery_agent.metrics import ROOT, read_queries
from dataquery_agent.sql_executor import BoundedQueryError, QueryResult, execute_bounded_readonly
from test_metadata_capability import fixture_snapshot

FIXTURE = json.loads((ROOT / "tests/fixtures/controlled_detail_v1.json").read_text())
WINDOW = FIXTURE["window"]
SEQ = "payment_failure_then_capture"
TRACE = {"trace_id": "test-agent"}


def reader_config():
    env = dotenv_values(ROOT / ".env.sqlshed-reader")
    return dict(host=env["SQLSHED_HOST"], port=env["SQLSHED_PORT"], dbname=env["SQLSHED_DATABASE"],
                user=env["SQLSHED_USER"], password=env["SQLSHED_PASSWORD"])


class DetailTests(unittest.TestCase):
    def test_versioned_validation_cases(self):
        for case in FIXTURE["validation_cases"]:
            with self.subTest(case=case["id"]):
                snapshot = fixture_snapshot()
                analysis = SEQ
                parameters = {**WINDOW, "group_by": ["channel"]}
                decision = CapabilityResolver(CapabilityRegistry(snapshot)).resolve(
                    {"analysis_id": analysis, **parameters})
                mutation = case["mutation"]
                if mutation == "missing_date":
                    parameters.pop("end_date")
                elif mutation == "forged_date":
                    parameters["start_date"] = "2025-01-05"
                elif mutation == "extra_filter":
                    parameters["filters"] = {"channel": "web"}
                elif mutation == "version":
                    parameters["template_version"] = "0.0.0"
                elif mutation == "unsupported_analysis":
                    analysis = "undefined"
                elif mutation == "wrong_status":
                    decision["status"] = "STANDARD_METRIC"
                elif mutation == "forged_join":
                    decision["join_paths"][0]["relationship_cardinality"] = "many_to_many"
                elif mutation == "missing_field":
                    decision["required_fields"]["dwd.payment_attempt_fact"].remove("paid_at")
                elif mutation == "schema_drift":
                    snapshot.schema_version = "different-version"
                    snapshot.get("dwd.payment_attempt_fact")["columns"].pop("paid_at")
                executor = DetailQueryExecutor("dbname=sqlshed user=postgres", lambda: snapshot)
                with patch("dataquery_agent.sql_executor.psycopg.connect") as connect:
                    result = executor.execute_detail(analysis, parameters, decision, TRACE)
                self.assertEqual(result["code"], case["code"], result)
                self.assertEqual(result["execution_trace"]["agent_trace_id"], TRACE["trace_id"])
                connect.assert_not_called()

    def test_registry_validates_every_variant_and_pinned_hash(self):
        registry = DetailTemplateRegistry()
        for key, definition in registry.contract["analyses"].items():
            for spec in definition["templates"]:
                template = registry.load(key, spec["dimensions"], spec["template_version"])
                self.assertEqual(template.template_id, spec["template_id"])
        registry.contract["analyses"][SEQ]["templates"][0]["sha256"] = "changed"
        with self.assertRaisesRegex(BoundedQueryError, "template_version_mismatch"):
            registry.load(SEQ, [])

    def test_sqlglot_blocks_writes_ods_unknown_functions_and_extra_statements(self):
        template = DetailTemplateRegistry().load(SEQ, [])
        where = "paid_at >= %(start)s AND paid_at < %(end)s"
        for statement in [
            f"DELETE FROM dwd.payment_attempt_fact WHERE {where}",
            f"SELECT order_id FROM ods.payments WHERE {where}",
            f"SELECT pg_sleep(1) FROM dwd.payment_attempt_fact WHERE {where}",
            f"SELECT order_id FROM dwd.payment_attempt_fact WHERE {where}; SELECT 1",
            f"SELECT order_id FROM dwd.payment_attempt_fact WHERE {where} FOR UPDATE",
        ]:
            with self.subTest(statement=statement), self.assertRaisesRegex(BoundedQueryError, "unsafe_template"):
                replace(template, statement=statement).validate_sql(statement)

    def test_result_schema_decimal_and_unique_grain(self):
        template = DetailTemplateRegistry().load("sales_by_order", ["order_id"])
        cols = ("order_id", "net_amount")
        self.assertEqual(validated_rows(template, QueryResult(cols, ((1, Decimal("0.10")),), False)),
                         [{"order_id": 1, "net_amount": "0.10"}])
        for rows in [((1, 0.1),), ((1, None),), ((1, Decimal("NaN")),),
                     ((1, Decimal("1")), (1, Decimal("2")))]:
            with self.assertRaisesRegex(BoundedQueryError, "invalid_result"):
                validated_rows(template, QueryResult(cols, rows, False))

    def test_timeout_and_cost_are_structured(self):
        template = DetailTemplateRegistry().load(SEQ, [])
        args = dict(dsn=f"dbname=sqlshed user={DETAIL_ROLE}", statement=template.statement,
                    parameters={"start": WINDOW["start_date"], "end": WINDOW["end_date"]},
                    role=DETAIL_ROLE, validate=template.validate_sql, authorize=lambda _: None)
        with patch("dataquery_agent.sql_executor.psycopg.connect", side_effect=psycopg.errors.QueryCanceled):
            with self.assertRaisesRegex(BoundedQueryError, "query_timeout"):
                execute_bounded_readonly(**args)
        with patch("dataquery_agent.sql_executor.psycopg.connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.side_effect = [
                (DETAIL_ROLE, DETAIL_ROLE, "sqlshed", "on"), ([{"Plan":{"Total Cost":100001}}],),
            ]
            with self.assertRaisesRegex(BoundedQueryError, "plan_cost_limit"):
                execute_bounded_readonly(**args)
            conn.cursor.assert_not_called()

    def test_agent_executes_detail_with_grounded_provider_variants(self):
        from unittest.mock import Mock

        from dataquery_agent.metric_query_service import MetricQueryService
        from dataquery_agent.unified_agent import UnifiedAgent

        service = MetricQueryService(None)
        service.query = Mock()
        resolver = CapabilityResolver(CapabilityRegistry(fixture_snapshot()))
        executor = Mock()
        executor.supports.return_value = True
        agent = UnifiedAgent(service, resolver, executor)
        variants = [
            (["订单"], [], ["先支付失败，后来成功支付"], [{"order_count": 0}]),
            (["订单数"], ["渠道"], [], []),
            (["商品净额"], ["订单"], [], [{"order_id": 13, "net_amount": "25.40"}]),
        ]
        for case, (metrics, groups, filters, rows) in zip(FIXTURE["live_cases"], variants):
            with self.subTest(case=case["id"]):
                executor.execute_detail.reset_mock()
                executor.execute_detail.return_value = {"status": "ok", "results": rows, "truncated": False,
                                                        "definition": "fixture", "execution_trace": {}}
                raw = json.dumps(dict(metrics=metrics, groups=groups, filters=filters, time="2025年1月",
                                      type="metric", missing=[]), ensure_ascii=False)
                answer = agent.answer(case["question"], lambda _: raw)
                self.assertEqual(answer["status"], "ok", answer)
                args = executor.execute_detail.call_args.args
                self.assertEqual(args[0], case["analysis"])
                self.assertEqual(args[1], dict(WINDOW, group_by=case["group_by"]))
        service.query.assert_not_called()


@unittest.skipUnless(os.environ.get("DETAIL_POSTGRES_TESTS") == "1", "Requires SQL Shed and detail reader")
class DetailPostgresTests(unittest.TestCase):
    def setUp(self):
        self.executor = DetailQueryExecutor.local()
        self.snapshot = self.executor.snapshot()
        self.resolver = CapabilityResolver(CapabilityRegistry(self.snapshot))

    def execute(self, analysis, axes):
        params = {**WINDOW, "group_by": axes}
        route = self.resolver.resolve({"analysis_id": analysis, **params})
        result = self.executor.execute_detail(analysis, params, route, TRACE)
        self.assertEqual(result["status"], "ok", result)
        return result

    def test_payment_total_and_channel_against_independent_ods(self):
        with psycopg.connect(**reader_config(), options="-c default_transaction_read_only=on") as pg:
            reference = read_queries(ROOT / "sql/warehouse/metadata/ground_truth.sql")["payment_sequence_ods"]
            args = {"start": WINDOW["start_date"], "end": WINDOW["end_date"]}
            orders = pg.execute(reference, args).fetchall()
            by_channel = pg.execute(
                "SELECT o.channel, count(*) FROM (" + reference.rstrip(";") +
                ") q JOIN ods.orders o USING(order_id) GROUP BY o.channel ORDER BY o.channel", args).fetchall()
        self.assertEqual(self.execute(SEQ, [])["results"], [{"order_count": len(orders)}])
        self.assertEqual(self.execute(SEQ, ["channel"])["results"],
                         [{"channel": channel, "order_count": count} for channel, count in by_channel])

    def test_sales_by_order_all_four_variants_against_ods(self):
        for axes in ([], ["category"], ["channel"], ["category", "channel"]):
            with self.subTest(axes=axes):
                keys = ["i.order_id"] + (["p.category_id"] if "category" in axes else [])
                keys += ["o.channel"] if "channel" in axes else []
                grouped = ", ".join(keys)
                query = (f"SELECT {grouped}, sum(i.quantity*i.unit_price-i.discount_amount) AS net_amount "
                         "FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id) "
                         "WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s "
                         f"GROUP BY {grouped} ORDER BY {grouped} LIMIT 101")
                with psycopg.connect(**reader_config(), options="-c default_transaction_read_only=on") as pg:
                    cursor = pg.execute(query, {"start": WINDOW["start_date"], "end": WINDOW["end_date"]})
                    expected = [dict(zip((col.name for col in cursor.description),
                                         (format(value, "f") if type(value) is Decimal else value for value in row)))
                                for row in cursor]
                result = self.execute("sales_by_order", ["order_id", *axes])
                self.assertEqual(result["results"], expected[:100])
                self.assertEqual(result["truncated"], len(expected) > 100)

    def test_exact_template_on_synthetic_temporal_boundaries(self):
        events = sql.SQL(",").join(sql.SQL("({}, {}, {}::timestamp)").format(*map(sql.Literal, row))
                                   for row in FIXTURE["synthetic_events"])
        channels = sql.SQL(",").join(sql.SQL("({}, {})").format(*map(sql.Literal, row))
                                     for row in FIXTURE["synthetic_channels"])
        prefix = sql.SQL("WITH events(order_id,status,paid_at) AS (VALUES {}), "
                         "orders(order_id,channel) AS (VALUES {}), ").format(events, channels)
        with psycopg.connect(**reader_config(), options="-c default_transaction_read_only=on") as pg:
            for axes, expected in [([], [(2,)]), (["channel"], [("mobile", 1), ("web", 1)])]:
                asset = DetailTemplateRegistry().load(SEQ, axes).statement
                body = asset.removeprefix("WITH ").replace("dwd.payment_attempt_fact", "events")
                body = body.replace("dwd.order_fact", "orders")
                actual = pg.execute(prefix + sql.SQL(body), {"start": WINDOW["start_date"],
                                                             "end": WINDOW["end_date"]}).fetchall()
                self.assertEqual(actual, expected)

    def test_effective_permissions_and_unauthorized_column(self):
        from dataquery_agent.detail_executor import authorize_detail

        with psycopg.connect(self.executor.dsn, autocommit=True) as pg:
            self.assertEqual(pg.execute("SELECT current_user,has_schema_privilege('ods','USAGE'), "
                                        "has_schema_privilege('dws','USAGE')").fetchone(),
                             (DETAIL_ROLE, False, False))
            with pg.transaction(force_rollback=True):
                pg.execute("SET TRANSACTION READ ONLY")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    authorize_detail(pg, self.snapshot, {"dwd.order_fact": ["customer_id"]})

    def test_in_transaction_schema_comparison_blocks_stale_snapshot(self):
        from dataquery_agent.detail_executor import authorize_detail

        stale = deepcopy(self.snapshot)
        stale.get("dwd.payment_attempt_fact")["columns"]["paid_at"]["type"] = "date"
        with psycopg.connect(self.executor.dsn, autocommit=True) as pg:
            with pg.transaction(force_rollback=True):
                pg.execute("SET TRANSACTION READ ONLY")
                with self.assertRaisesRegex(BoundedQueryError, "schema_drift"):
                    authorize_detail(pg, stale, {"dwd.payment_attempt_fact": ["paid_at"]})

    def test_full_window_zero_matches_ods(self):
        parameters = {"start_date": "2023-01-01", "end_date": "2026-01-01", "group_by": []}
        route = self.resolver.resolve({"analysis_id": SEQ, **parameters})
        result = self.executor.execute_detail(SEQ, parameters, route, TRACE)
        with psycopg.connect(**reader_config(), options="-c default_transaction_read_only=on") as pg:
            reference = read_queries(ROOT / "sql/warehouse/metadata/ground_truth.sql")["payment_sequence_ods"]
            expected = pg.execute(reference, {"start": parameters["start_date"],
                                               "end": parameters["end_date"]}).fetchall()
        self.assertEqual(expected, [])
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["results"], [{"order_count": 0}])

    def test_postgres_enforces_statement_timeout(self):
        template = DetailTemplateRegistry().load(SEQ, [])
        with patch("dataquery_agent.sql_executor.STATEMENT_TIMEOUT_MS", 20):
            with self.assertRaisesRegex(BoundedQueryError, "query_timeout"):
                execute_bounded_readonly(
                    self.executor.dsn, template.statement, {"start": WINDOW["start_date"], "end": WINDOW["end_date"]},
                    role=DETAIL_ROLE, validate=template.validate_sql,
                    authorize=lambda pg: pg.execute("SELECT pg_sleep(0.2)"),
                )
