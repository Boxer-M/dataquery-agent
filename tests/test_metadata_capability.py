import json
import os
import re
import unittest
from copy import deepcopy

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from dataquery_agent.capability_registry import CapabilityRegistry, link_key
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.metadata_service import MetadataService, MetadataSnapshot, manifest_models
from dataquery_agent.metrics import ROOT, read_queries

FIXTURE = json.loads((ROOT / "tests/fixtures/metadata_capability_v1.json").read_text())
WINDOW = FIXTURE["window"]


def fixture_snapshot():
    """Synthetic catalog based on manifest expectations. It has no business rows."""
    relations = {}
    for name, model in manifest_models().items():
        constraints = []
        for definition in model.get("constraints", []):
            match = re.match(r"(PRIMARY KEY|FOREIGN KEY|UNIQUE) \(([^)]+)\)", definition)
            fk = re.search(r"REFERENCES ([\w.]+)\(([^)]+)\)", definition)
            constraints.append({
                "definition": definition, "validated": True,
                "kind": {"PRIMARY KEY": "p", "FOREIGN KEY": "f", "UNIQUE": "u"}.get(
                    match[1] if match else "", "c"),
                "columns": match[2].split(", ") if match else [],
                "target": fk[1] if fk else None,
                "target_columns": fk[2].split(", ") if fk else [],
            })
        relations[name] = {
            "name": name, "kind": "v" if name.startswith("ads.") else "r", "rls": False,
            "schema_usage": True, "metric_schema_usage": True, "constraints": constraints,
            "columns": {column["name"]: dict(column, readable=True, metric_readable=True)
                        for column in model["columns"]},
        }
    return MetadataSnapshot(relations, "fixture-schema", "synthetic metadata fixture")


def evaluate(test, snapshot, *, include_mutations):
    for case in FIXTURE["cases"]:
        mutation = case.get("mutation")
        if mutation and not include_mutations:
            continue
        with test.subTest(case=case["id"]):
            changed = deepcopy(snapshot) if mutation else snapshot
            if mutation == "column_type":
                changed.get("dws.order_day")["columns"]["order_count"]["type"] = "text"
            if mutation == "deny_metric_column":
                changed.get("dws.order_day")["columns"]["order_count"]["metric_readable"] = False
            result = CapabilityResolver(CapabilityRegistry(changed)).resolve(WINDOW | case["request"])
            test.assertEqual((result["status"], result["reason_code"]), (case["status"], case["code"]), result)
            test.assertEqual(result["selected_source"], case.get("source"), result)
            if "time" in case:
                test.assertEqual(result["time_constraints"]["event_time"], case["time"])
            if "grain" in case:
                test.assertEqual(result["grain"], case["grain"])
            if "edge" in case:
                matching = [edge for edge in result["join_paths"] if list(link_key(edge)) == case["edge"]]
                test.assertEqual(len(matching), 1, result)
                test.assertEqual(matching[0]["relationship_cardinality"], "many_to_one")
            for source, fields in case.get("fields", {}).items():
                test.assertLessEqual(set(fields), set(result["required_fields"][source]))
            if "reason" in case:
                test.assertIn(case["reason"], result["reason"])
            if "risk" in case:
                test.assertIn(case["risk"], " ".join(result["risks"]))
            if "rejected" in case:
                rejected = [candidate["source"] for candidate in result["candidate_sources"] if not candidate["selected"]]
                test.assertIn(case["rejected"], rejected)
            if result["status"] == "DETAIL_QUERY":
                test.assertEqual(result["execution_policy"], "plan_only")


class CapabilityUnitTests(unittest.TestCase):
    def test_versioned_business_cases(self):
        evaluate(self, fixture_snapshot(), include_mutations=True)

    def test_access_changes_version_without_changing_schema(self):
        snapshot = fixture_snapshot()
        original = CapabilityRegistry(snapshot)
        changed = deepcopy(snapshot)
        changed.get("dws.payment_day")["metric_schema_usage"] = False
        revised = CapabilityRegistry(changed)
        self.assertNotEqual(revised.version, original.version)
        self.assertEqual(revised.snapshot.schema_version, original.snapshot.schema_version)

    def test_missing_unique_key_blocks_logical_join(self):
        snapshot = fixture_snapshot()
        snapshot.get("dim.product")["constraints"] = []
        registry = CapabilityRegistry(snapshot)
        self.assertIn(("dws.product_sales_day", "dim.product", "product_id", "product_id"), registry.invalid_joins)
        result = CapabilityResolver(registry).resolve(WINDOW | {
            "metric_id": "item_net_sales_amount", "group_by": ["category"],
        })
        self.assertEqual(result["reason_code"], "schema_drift")

    def test_admin_config_rejected(self):
        with self.assertRaises(ValueError):
            MetadataService({"SQLSHED_USER": "dataquery_admin", "SQLSHED_DATABASE": "sqlshed"})


@unittest.skipUnless(os.environ.get("CAPABILITY_POSTGRES_TESTS") == "1", "Requires local SQL Shed")
class CapabilityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = MetadataService.local()
        cls.snapshot = cls.metadata.snapshot()
        cls.registry = CapabilityRegistry(cls.snapshot)

    def test_live_metadata_and_business_routing(self):
        self.assertEqual(len(self.snapshot.relations), 25)
        self.assertEqual(len(self.registry.models), 18)
        self.assertEqual(self.registry.errors, {})
        self.assertEqual(self.registry.invalid_joins, {})
        self.assertEqual(len(self.registry.joins), 15)
        self.assertFalse(self.snapshot.get("ods.orders")["metric_schema_usage"])
        self.assertFalse(self.snapshot.get("dim.customer")["columns"]["city"]["metric_readable"])
        evaluate(self, self.snapshot, include_mutations=False)

    def test_all_standard_routes_use_only_actual_readable_fields(self):
        resolver = CapabilityResolver(self.registry)
        for mid in self.registry.standard:
            with self.subTest(metric=mid):
                required = {"category_sales_share": ["category"], "channel_order_share": ["channel"]}
                result = resolver.resolve(WINDOW | {"metric_id": mid, "group_by": required.get(mid, [])})
                self.assertEqual(result["status"], "STANDARD_METRIC", result)
                self.assertTrue(result["selected_source"].startswith("dws."))
                self.assertTrue(all(self.registry.readable(name, columns, standard=True)
                                    for name, columns in result["required_fields"].items()))

    def test_business_ground_truth_with_readonly_accounts(self):
        from dataquery_agent.dws import metric_query
        from dataquery_agent.metric_query_service import MetricQueryService

        config = self.metadata.config
        service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query")["METRIC_QUERY_DSN"])
        resolver = CapabilityResolver(self.registry)
        params = dict(start=WINDOW["start_date"], end=WINDOW["end_date"], channel=None, category=None,
                      as_of="2026-01-01")
        with psycopg.connect(host=config["SQLSHED_HOST"], port=config["SQLSHED_PORT"],
                             dbname=config["SQLSHED_DATABASE"], user=config["SQLSHED_USER"],
                             password=config["SQLSHED_PASSWORD"], row_factory=dict_row) as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '10s'")
            queries = read_queries(ROOT / "sql/warehouse/metadata/ground_truth.sql")
            route = resolver.resolve(WINDOW | {"metric_id": "payment_attempt_success_rate"})
            result = service.query(route["metric_request"])
            self.assertEqual(result["status"], "ok", result)
            reference = read_queries(ROOT / "sql/warehouse/dws_metrics/ods.sql")
            expected = pg.execute(metric_query(pg, reference["payment_attempt_success_rate"]), params).fetchone()
            actual = result["results"][0]
            for field in ("value", "numerator", "denominator"):
                self.assertEqual(str(expected[field]), str(actual[field]))
            for topic in ("payment_sequence", "sales_by_order"):
                lhs = pg.execute(queries[f"{topic}_ods"], params).fetchall()
                rhs = pg.execute(queries[f"{topic}_dwd"], params).fetchall()
                self.assertEqual(lhs, rhs, topic)
                if topic == "sales_by_order":
                    self.assertTrue(lhs)
                else:
                    self.assertEqual(lhs, [])

    def test_payment_sequence_sql_distinguishes_ties_reverse_and_boundary(self):
        config = self.metadata.config
        query = read_queries(ROOT / "sql/warehouse/metadata/ground_truth.sql")["payment_sequence_dwd"]
        fixture = """
        WITH events(order_id, status, paid_at) AS (
          VALUES (1, 'failed', timestamp '2025-01-02'), (1, 'captured', timestamp '2025-01-03'),
                 (2, 'failed', timestamp '2025-01-02'), (2, 'captured', timestamp '2025-01-02'),
                 (3, 'failed', timestamp '2025-01-03'), (3, 'captured', timestamp '2025-01-02'),
                 (4, 'failed', timestamp '2025-01-31'), (4, 'captured', timestamp '2025-02-01')
        )
        """
        with psycopg.connect(host=config["SQLSHED_HOST"], port=config["SQLSHED_PORT"],
                             dbname=config["SQLSHED_DATABASE"], user=config["SQLSHED_USER"],
                             password=config["SQLSHED_PASSWORD"]) as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            rows = pg.execute(fixture + query.replace("dwd.payment_attempt_fact", "events"),
                              {"start": WINDOW["start_date"], "end": WINDOW["end_date"]}).fetchall()
        self.assertEqual(rows, [(1,)])
