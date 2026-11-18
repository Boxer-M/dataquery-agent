import json
import os
import unittest
from secrets import token_hex
from unittest.mock import patch
from uuid import uuid4

import psycopg

from dataquery_agent.api_models import public_response
from dataquery_agent.catalog_recovery import sql_checkpoint
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import owner_hash
from dataquery_agent.performance import Performance
from dataquery_agent.rag.documents import catalog_document
from test_business_planner_v2 import PROPOSE, QUESTION, TODAY


@unittest.skipUnless(os.getenv("KNOWLEDGE_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class CatalogRecoveryTests(unittest.TestCase):
    def test_restore_only_missing_tasks_and_keep_attempt_history(self):
        manager, owner = local_manager()
        manager.generate = lambda _: json.dumps(PROPOSE)
        service = manager.agent.service
        cid = manager.create_conversation(owner)["conversation_id"]
        store = manager.agent.knowledge.retriever.store
        performance = Performance.local()

        def process_message(current_owner, *args, **kwargs):
            with performance.scope(owner_hash(current_owner)):
                return manager.process_message(current_owner, *args, **kwargs)

        with patch.object(service, "query", wraps=service.query) as query:
            # Simulate an index outage without mutating the shared Catalog registry.
            with patch.object(store, "catalog_chunks", return_value=[]) as lookup:
                original = process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
                self.assertEqual(original["status"], "partial", original)
                initial = manager.get_query(owner, original["query_id"])
                self.assertIn("recovery", initial)
                command = f"recover-catalog {original['query_id']}"
                message_id = uuid4()
                missing = process_message(owner, cid, command, message_id, today=TODAY)
                self.assertEqual(missing["status"], "partial", missing)
                duplicate = process_message(owner, cid, command, message_id, today=TODAY)
                self.assertTrue(duplicate["replayed"])
                self.assertEqual(lookup.call_count, 3)  # Two initial nodes and one batched recovery.
            denied = process_message(token_hex(32), cid, command, uuid4(), today=TODAY)
            self.assertEqual(denied["code"], "conversation_not_found")
            with patch.dict(service.metrics["order_count"], definition="changed for test"):
                stale = process_message(owner, cid, command, uuid4(), today=TODAY)
            self.assertEqual(stale["code"], "recovery_checkpoint_invalid", stale)
            with patch.object(service, "dsn", None):
                unavailable = process_message(owner, cid, command, uuid4(), today=TODAY)
            self.assertEqual(unavailable["code"], "recovery_checkpoint_invalid")
            recovered = process_message(owner, cid, command, uuid4(), today=TODAY)
            self.assertEqual(recovered["status"], "ok", recovered)
            final = manager.get_query(owner, recovered["query_id"])
            self.assertEqual(final["recovery"]["status"], "completed")
            self.assertEqual(final["recovery"]["plan_id"], initial["recovery"]["plan_id"])
            self.assertEqual(final["recovery"]["previous_query_id"], missing["query_id"])
            self.assertEqual(final["recovery"]["results"], initial["recovery"]["results"])
            self.assertEqual(final["subtasks"][:2], initial["subtasks"][:2])
            self.assertEqual([task["task_id"] for task in final["subtasks"]],
                             [task["task_id"] for task in initial["subtasks"]])
            self.assertTrue(all(task["status"] == "ok" for task in final["subtasks"]))
            self.assertNotEqual(final["subtasks"][2]["attempt_id"], initial["subtasks"][2]["attempt_id"])
            self.assertEqual(manager.get_query(owner, original["query_id"]), initial)
            live_trace = public_response(recovered).trace
            saved_trace = public_response(final, history=True).trace
            self.assertEqual(live_trace, saved_trace)
            self.assertEqual(str(saved_trace.previous_query_id), missing["query_id"])
            self.assertEqual(len({task.attempt_id for task in saved_trace.tasks}), 2)
            self.assertEqual(str(saved_trace.run_id), recovered["query_id"])
            tool, = saved_trace.tools
            self.assertEqual((tool.plan_id, tool.attempt_id), (saved_trace.plan_id, saved_trace.attempt_id))
            self.assertIsNone(tool.task_id)  # One lookup actually served both restored nodes.
            self.assertIsNone(saved_trace.tasks[2].schedule_position)
            self.assertEqual(query.call_count, 2)
            replay = process_message(owner, cid, command, uuid4(), today=TODAY)
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["query_id"], recovered["query_id"])
            self.assertEqual(query.call_count, 2)
        if path := os.getenv("CATALOG_RECOVERY_REPORT"):
            from pathlib import Path
            Path(path).write_text(json.dumps({
                "index_outage": "simulated empty lookup; restored lookups use PostgreSQL",
                "initial": initial, "missing_attempt": manager.get_query(owner, missing["query_id"]),
                "recovered": final, "recovery_trace": recovered["trace"], "duplicate_query_id": replay["query_id"],
                "metric_calls_initial": 2, "metric_calls_during_recovery": query.call_count - 2,
                "denied_owner": denied["code"], "changed_catalog": stale["code"],
            }, ensure_ascii=False, default=str, indent=2) + "\n")

    def test_concurrent_transaction_still_prevents_recovery_checkpoint(self):
        manager, _ = local_manager()
        owner = token_hex(32)
        manager.generate = lambda _: json.dumps(PROPOSE)
        cid = manager.create_conversation(owner)["conversation_id"]
        service, proofs = manager.agent.service, []
        query = service.query
        committed = False

        def capture(*args, **kwargs):
            proof = sql_checkpoint(*args, **kwargs)
            proofs.append(proof)
            return proof

        def execute(request):
            nonlocal committed
            result = query(request)
            if not committed:
                # Assign and commit an XID without changing any warehouse rows.
                with manager.store.transaction() as pg:
                    pg.execute("SELECT pg_current_xact_id()")
                committed = True
            return result

        with patch("dataquery_agent.catalog_recovery.sql_checkpoint", side_effect=capture), \
                patch.object(service, "query", side_effect=execute) as calls, \
                patch.object(manager.agent.knowledge.retriever.store, "catalog_chunks", return_value=[]):
            result = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
        self.assertEqual(result["status"], "partial", result)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(len(proofs), 2)
        self.assertTrue(all(proofs))
        self.assertNotEqual(proofs[0]["snapshot"], proofs[1]["snapshot"])
        contents = [{key: value for key, value in proof.items() if key != "snapshot"} for proof in proofs]
        self.assertEqual(contents[0], contents[1])
        self.assertNotIn("recovery", result["result"])
        self.assertNotIn("recovery", manager.get_query(owner, result["query_id"]))

    def test_catalog_failures_are_distinct(self):
        manager, owner = local_manager()
        agent = manager.agent
        store = agent.knowledge.retriever.store
        resolution = {"method": "test", "metric_ids": ["order_count"]}
        for error, code in (
            (psycopg.errors.InsufficientPrivilege(), "catalog_permission_denied"),
            (psycopg.OperationalError(), "catalog_service_unavailable"),
        ):
            with patch.object(store, "catalog_chunks", side_effect=error):
                result = agent.knowledge.exact(agent.service, resolution, today=TODAY, owner=owner)
            self.assertEqual(result["retrieval_trace"]["metric_errors"]["order_count"], code)
        rows = store.catalog_chunks(
            [catalog_document(
                "order_count", agent.service.metrics["order_count"], agent.service.catalog["catalog_version"])],
            today=TODAY, owner=owner)
        self.assertTrue(rows)
        with patch.object(store, "catalog_chunks", return_value=[dict(rows[0], document_version="old")]):
            result = agent.knowledge.exact(agent.service, resolution, today=TODAY, owner=owner)
        self.assertEqual(result["retrieval_trace"]["metric_errors"]["order_count"], "catalog_index_stale")
        self.assertEqual(result["citations"], [])
