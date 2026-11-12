import os
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import psycopg

from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.conversation_store import ConversationError
from dataquery_agent.dynamic_context import compile_blocks, query_blocks
from dataquery_agent.dynamic_sql_engine import DynamicSqlEngine
from dataquery_agent.dynamic_validation import CandidateError
from dataquery_agent.metadata_service import fingerprint
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.query_asset_contract import (
    EXAMPLE, OPERATION, analyze_asset_impact, asset_context, canonical_sql, contract, dependencies, semantic_sql,
)
from dataquery_agent.query_asset_governance import QueryAssetGovernance, QueryAssetRuntime, reference_rows
from dataquery_agent.query_asset_store import AssetError, QueryAssetStore
from dataquery_agent.unified_agent import UnifiedAgent
from evaluate_dynamic_sql import TODAY
from test_metadata_capability import fixture_snapshot

STATEMENT = compile_blocks(query_blocks(EXAMPLE))
QUESTION = "2025年1月各渠道按支付尝试次数列前3订单"


class QueryAssetTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture_snapshot()
        self.engine = DynamicSqlEngine(None, lambda: self.snapshot)
        self.business = contract()
        self.definition = {"semantic_hash": fingerprint(self.business),
                           "dependencies": dependencies(self.snapshot, self.business)}

    def test_parameterization_and_semantic_proof(self):
        _, context = asset_context(EXAMPLE, self.snapshot)
        self.assertEqual(canonical_sql(STATEMENT), canonical_sql("\n" + STATEMENT + " ;"))
        for value in ("'2025-01-01'", "'web'"):
            with self.assertRaisesRegex(AssetError, "requires_parameters"):
                canonical_sql(STATEMENT.replace("%(start)s", value))
        renamed = STATEMENT.replace("grouped", "asset_grouped")
        self.assertNotEqual(canonical_sql(STATEMENT), canonical_sql(renamed))
        self.engine.validate(semantic_sql(renamed), context)
        with self.assertRaisesRegex(CandidateError, "semantic_mismatch"):
            self.engine.validate(STATEMENT.replace("DESC", "ASC"), context)
        with self.assertRaisesRegex(CandidateError, "unsafe_sql"):
            self.engine.ast.validate("SELECT * FROM agent_meta.query_asset_versions", context)

    def test_dependency_change_classification(self):
        model = "dwd.payment_attempt_fact"
        for change in ("delete", "rename", "type", "add", "key"):
            with self.subTest(change=change):
                snapshot = deepcopy(self.snapshot)
                relation = snapshot.get(model)
                if change == "delete":
                    relation["columns"].pop("paid_at")
                elif change == "rename":
                    relation["columns"]["payment_event_time"] = relation["columns"].pop("paid_at")
                elif change == "type":
                    relation["columns"]["paid_at"]["type"] = "text"
                elif change == "add":
                    relation["columns"]["unused"] = {"type": "text", "nullable": True}
                else:
                    relation["constraints"].clear()
                impact = analyze_asset_impact(self.definition, snapshot)
                self.assertEqual(impact["classification"], "unrelated" if change == "add" else "incompatible")
                if change == "add":
                    asset_context(EXAMPLE, snapshot)

    def test_business_change_with_identical_sql_needs_review(self):
        for mutate in ("version", "time"):
            business = deepcopy(self.business)
            if mutate == "version":
                business["semantic_contract_version"] = "2.0.0"
            else:
                business["specification"]["event_time"] = "order_time"
            with patch("dataquery_agent.query_asset_contract.contract", return_value=business):
                self.assertEqual(analyze_asset_impact(self.definition, self.snapshot)["classification"],
                                 "business_uncertain")
            self.assertNotEqual(fingerprint(business), self.definition["semantic_hash"])

    def test_runtime_fails_closed_when_registry_unavailable(self):
        store = Mock()
        store.active.side_effect = psycopg.OperationalError("database unavailable")
        engine = Mock()
        result = QueryAssetRuntime(store, engine).execute(QUESTION, EXAMPLE, agent_trace_id="test")
        self.assertEqual(result["code"], "asset_unavailable")
        engine.execute_approved.assert_not_called()


    def test_independent_ranking_empty_and_ties(self):
        self.assertEqual(reference_rows([], 1), [])
        self.assertEqual(reference_rows([("web", 5, 3), ("web", 2, 3), ("mobile", 5, 7)], 1),
                         [{"channel": "mobile", "order_id": 5, "attempt_count": 7, "rank": 1},
                          {"channel": "web", "order_id": 2, "attempt_count": 3, "rank": 1}])

    def test_history_requires_owner_and_never_copies_values(self):
        conversations = Mock()
        conversations.history.side_effect = ConversationError("query_not_found")
        governance = QueryAssetGovernance(Mock(), self.engine)
        with self.assertRaisesRegex(ConversationError, "query_not_found"):
            governance.from_history(conversations, "owner", "query")
        governance.store.create.assert_not_called()
        conversations.history.assert_called_once_with("owner", "query")
        history = {"execution_strategy": "dynamic_sql", "execution_status": "ok", "analysis_id": OPERATION,
                   "sql": STATEMENT, "parameter_summary": EXAMPLE.parameters()}
        conversations.history.side_effect = None
        conversations.history.return_value = history
        from uuid import uuid4
        source_id = uuid4()
        governance.from_history(conversations, "owner", source_id)
        first = governance.store.create.call_args.args[0]
        history["parameter_summary"] = EXAMPLE.parameters() | {"top_n": 1}
        governance.from_history(conversations, "owner", uuid4())
        second = governance.store.create.call_args.args[0]
        self.assertEqual(first["canonical_sql"], second["canonical_sql"])
        self.assertEqual(first["semantic_hash"], second["semantic_hash"])
        self.assertNotIn(EXAMPLE.start, first["canonical_sql"])
        self.assertNotIn("parameter_summary", first)

    def test_candidate_does_not_execute_and_other_routes_ignore_assets(self):
        from contextlib import nullcontext
        from dataquery_agent.dynamic_requirements import recognize
        store = Mock()
        store.active.return_value = nullcontext(None)
        engine = Mock()
        runtime = QueryAssetRuntime(store, engine)
        req = recognize(QUESTION, TODAY)
        self.assertIsNone(runtime.execute(QUESTION, req, agent_trace_id="test"))
        engine.execute_approved.assert_not_called()
        agent = UnifiedAgent(MetricQueryService(None), CapabilityResolver(CapabilityRegistry(self.snapshot)),
                             dynamic_engine=engine, asset_runtime=Mock())
        def fixed_intent(_prompt):
            return '{"metrics":["订单量"],"time":"2025年1月","groups":[],"filters":[],"type":"metric","missing":[]}'
        agent.answer("2025年1月订单量", fixed_intent, today=TODAY)
        agent.asset_runtime.execute.assert_not_called()
        agent.answer("2025年1月同订单先失败后成功支付的订单数", lambda _: "{}", today=TODAY)
        agent.asset_runtime.execute.assert_not_called()


@unittest.skipUnless(os.getenv("QUERY_ASSET_POSTGRES_TESTS") == "1", "query asset PostgreSQL opt-in")
class QueryAssetPostgresTests(unittest.TestCase):
    def test_actual_runtime_reuses_latest_without_model_and_checks_drift(self):
        from datetime import date
        from dataquery_agent.query_asset_governance import ods_truth
        engine = DynamicSqlEngine.local()
        reader = QueryAssetStore.local()
        with reader.active(OPERATION) as row:
            if not row or row["lifecycle_status"] != "PUBLISHED":
                self.skipTest("run the asset lifecycle demo on an empty registry first")
            version = row["implementation_version"]
        agent = UnifiedAgent(MetricQueryService(None), CapabilityResolver(CapabilityRegistry(engine.snapshot())),
                             dynamic_engine=engine, asset_runtime=QueryAssetRuntime(reader, engine))
        backend = Mock(side_effect=AssertionError("model should not run"))
        answer = agent.answer(QUESTION, backend, today=date(2025, 3, 10))
        self.assertEqual(answer["status"], "ok")
        result = answer["result"]
        self.assertEqual(result["query_asset"]["implementation_version"], version)
        self.assertEqual((result["results"], result["truncated"]), ods_truth(EXAMPLE))
        snapshot = engine.snapshot()
        columns = snapshot.get("dwd.payment_attempt_fact")["columns"]
        columns["paid_at"]["type"] = "text"
        with patch.object(engine, "snapshot", return_value=snapshot), patch.object(engine, "execute_approved") as execute:
            blocked = agent.answer(QUESTION, backend, today=date(2025, 3, 10))
            self.assertEqual(blocked["code"], "asset_metadata_stale")
            execute.assert_not_called()
        backend.assert_not_called()

    def test_roles_cannot_access_other_surfaces(self):
        reader = QueryAssetStore.local()
        with reader.transaction() as pg:
            self.assertEqual(pg.execute("""
                SELECT has_table_privilege('agent_meta.query_asset_versions','SELECT') AS registry,
                       has_table_privilege('agent_meta.query_asset_versions','INSERT,UPDATE,DELETE') AS mutation,
                       has_table_privilege('agent_meta.query_history','SELECT') AS history
            """).fetchone(), {"registry": True, "mutation": False, "history": False})
        with psycopg.connect(DynamicSqlEngine.local().dsn) as pg:
            self.assertEqual(pg.execute("SELECT has_schema_privilege('agent_meta','USAGE')").fetchone(), (False,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                pg.execute("UPDATE agent_meta.query_asset_versions SET lifecycle_status='PUBLISHED'")

    def test_concurrent_detail_import_is_idempotent_and_cannot_publish(self):
        from concurrent.futures import ThreadPoolExecutor
        store = QueryAssetStore.local(admin=True)
        governance = QueryAssetGovernance(store, DynamicSqlEngine.local())
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _: governance.from_detail("payment_failure_then_capture", []), range(4)))
        self.assertTrue(all(row == rows[0] for row in rows))
        row = rows[0]
        with self.assertRaisesRegex(AssetError, "invalid_asset_transition"):
            governance.publish(row["asset_id"], row["implementation_version"])

    def test_invalid_calculation_rejected_by_real_pipeline(self):
        store = QueryAssetStore.local(admin=True)
        governance = QueryAssetGovernance(store, DynamicSqlEngine.local())
        row = governance.propose(STATEMENT.replace("DESC", "ASC"), {"type": "synthetic_bad_calculation"})
        ids = row["asset_id"], row["implementation_version"]
        if row["lifecycle_status"] == "CANDIDATE":
            row = governance.verify(*ids)
        self.assertEqual(row["lifecycle_status"], "REJECTED")
        with self.assertRaisesRegex(AssetError, "invalid_asset_transition"):
            governance.publish(*ids)


    def test_publication_rollback_keeps_old_version_active(self):
        store = QueryAssetStore.local(admin=True)
        engine = DynamicSqlEngine.local()
        governance = QueryAssetGovernance(store, engine)
        reader = QueryAssetStore.local()
        with reader.active(OPERATION) as before:
            prior_version = before and before["implementation_version"]
        row = governance.propose(STATEMENT.replace("ranked", "asset_ranked"),
                                 {"type": "transaction_rollback_test"})
        ids = row["asset_id"], row["implementation_version"]
        if row["lifecycle_status"] == "CANDIDATE":
            row = governance.verify(*ids)
        self.assertEqual(row["lifecycle_status"], "VALIDATED")

        original_event = store.event

        def fail_publication(pg, version, status, evidence=None):
            if status == "PUBLISHED":
                pg.execute("SELECT 1 / 0")
            return original_event(pg, version, status, evidence)

        with patch.object(store, "event", side_effect=fail_publication):
            with self.assertRaises(psycopg.errors.DivisionByZero):
                governance.publish(*ids)
        self.assertEqual(store.read(*ids)["lifecycle_status"], "VALIDATED")
        with reader.active(OPERATION) as after:
            self.assertEqual(after and after["implementation_version"], prior_version)


if __name__ == "__main__":
    unittest.main()
