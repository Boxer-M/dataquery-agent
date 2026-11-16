import json
import os
import unittest
from secrets import token_hex
from unittest.mock import patch
from uuid import uuid4

import psycopg

from dataquery_agent.conversation_manager import local_manager
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
        with patch.object(service, "query", wraps=service.query) as query:
            # Simulate an index outage without mutating the shared Catalog registry.
            with patch.object(store, "catalog_chunks", return_value=[]) as lookup:
                original = manager.process_message(owner, cid, QUESTION, uuid4(), today=TODAY)
                self.assertEqual(original["status"], "partial", original)
                initial = manager.get_query(owner, original["query_id"])
                self.assertIn("recovery", initial)
                command = f"recover-catalog {original['query_id']}"
                message_id = uuid4()
                missing = manager.process_message(owner, cid, command, message_id, today=TODAY)
                self.assertEqual(missing["status"], "partial", missing)
                duplicate = manager.process_message(owner, cid, command, message_id, today=TODAY)
                self.assertTrue(duplicate["replayed"])
                self.assertEqual(lookup.call_count, 2)
            denied = manager.process_message(token_hex(32), cid, command, uuid4(), today=TODAY)
            self.assertEqual(denied["code"], "conversation_not_found")
            with patch.dict(service.metrics["order_count"], definition="changed for test"):
                stale = manager.process_message(owner, cid, command, uuid4(), today=TODAY)
            self.assertEqual(stale["code"], "recovery_checkpoint_invalid", stale)
            with patch.object(service, "dsn", None):
                unavailable = manager.process_message(owner, cid, command, uuid4(), today=TODAY)
            self.assertEqual(unavailable["code"], "recovery_checkpoint_invalid")
            recovered = manager.process_message(owner, cid, command, uuid4(), today=TODAY)
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
            self.assertEqual(query.call_count, 2)
            replay = manager.process_message(owner, cid, command, uuid4(), today=TODAY)
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
