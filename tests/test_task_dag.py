import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.task_dag import MAX_TASKS, Task, TaskGraphError, execute_tasks
from evaluate_business_planning import reference_connection, reference_rows
from test_business_planner_v2 import PROPOSE, QUESTION, TODAY


class TaskDagTests(unittest.TestCase):
    def test_orders_dependencies_and_skips_only_dependents(self):
        tasks = [Task("catalog-1", "catalog", {"id": "catalog-1"}, ("metric-1",)),
                 Task("catalog-2", "catalog", {"id": "catalog-2"}, ("metric-2",)),
                 Task("metric-1", "metric", {"id": "metric-1"}),
                 Task("metric-2", "metric", {"id": "metric-2"})]
        calls = []

        def execute(params):
            calls.append(params["id"])
            return {"status": "error" if params["id"] == "metric-1" else "ok"}, None

        events = execute_tasks(tasks, {"metric": execute, "catalog": execute}, attempt_id="attempt",
                               deadline=1, clock=lambda: 0)
        self.assertEqual(calls, ["metric-1", "metric-2", "catalog-2"])
        self.assertEqual(events["catalog-1"][0]["error_code"], "dependency_failed")
        self.assertEqual(events["catalog-1"][0]["failed_dependencies"], ["metric-1"])
        self.assertEqual(events["catalog-2"][0]["status"], "ok")

    def test_invalid_graph_never_calls_handler(self):
        for tasks, code in [
            ([Task("a", "metric", {}, ("unknown",))], "missing_dependency"),
            ([Task("a", "metric", {}, ("b",)), Task("b", "metric", {}, ("a",))], "dependency_cycle"),
            ([Task("a", "metric", {}), Task("a", "catalog", {})], "duplicate_task_id"),
            ([Task("a", "sql", {})], "unsupported_task_type"),
            ([Task(str(i), "metric", {}) for i in range(MAX_TASKS + 1)], "task_limit"),
        ]:
            with self.subTest(code=code), self.assertRaisesRegex(TaskGraphError, code):
                execute_tasks(tasks, {"metric": self.fail, "catalog": self.fail}, attempt_id="attempt",
                              deadline=1, clock=lambda: 0)


@unittest.skipUnless(os.getenv("KNOWLEDGE_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class TaskDagPostgresTests(unittest.TestCase):
    def test_partial_graph_keeps_independent_verified_metric_and_citation(self):
        manager, owner = local_manager()
        manager.generate = lambda _: json.dumps(PROPOSE)
        cid = manager.create_conversation(owner)["conversation_id"]
        service = manager.agent.service
        query = service.query
        calls = []

        def execute(request):
            calls.append(request["metric_id"])
            if request["metric_id"] == "order_count":
                return {"status": "error", "error": {"code": "simulated_timeout"}}
            return query(request)

        message_id = uuid4()
        with patch.object(service, "query", side_effect=execute):
            answer = manager.process_message(owner, cid, QUESTION, message_id, today=TODAY)
            replay = manager.process_message(owner, cid, QUESTION, message_id, today=TODAY)
        self.assertEqual(answer["status"], "partial", answer)
        self.assertEqual(calls, ["order_count", "order_item_count"])
        self.assertTrue(replay["replayed"])
        result = answer["result"]
        first, second, blocked, cited = result["subtasks"]
        self.assertEqual([task["status"] for task in result["subtasks"]], ["error", "ok", "skipped", "ok"])
        self.assertEqual(blocked["error_code"], "dependency_failed")
        self.assertEqual(blocked["failed_dependencies"], [first["task_id"]])
        self.assertEqual(cited["depends_on"], [second["task_id"]])
        self.assertEqual(cited["citation"]["quote"], service.metrics["order_item_count"]["definition"])
        with reference_connection() as pg:
            expected = reference_rows(pg, second["request"])["all"]["value"]
        self.assertEqual(int(result["results"][0]["metrics"]["order_item_count"]["value"]), expected)
        self.assertIsNone(result["results"][0]["metrics"]["order_count"])
        history = manager.get_query(owner, answer["query_id"])
        self.assertEqual(history["subtasks"], result["subtasks"])
        self.assertEqual(history["plan_id"], result["plan_id"])
        self.assertFalse(history["knowledge"]["query_embedding_called"])
        if path := os.getenv("TASK_DAG_REPORT"):
            from pathlib import Path
            Path(path).write_text(json.dumps({
                "injected_failure": "order_count timeout", "ods_order_item_count": expected,
                "status": answer["status"], "subtasks": history["subtasks"], "trace": answer["trace"],
                "plan_id": history["plan_id"], "attempt_id": history["attempt_id"], "replayed": replay["replayed"],
            }, default=str, ensure_ascii=False, indent=2) + "\n")
