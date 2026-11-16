import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from dataquery_agent.capability_trace import capability_link
from dataquery_agent.conversation_manager import local_manager
from evaluate_agent_e2e import TODAY, evaluate_capture
from evaluate_business_planning import reference_connection

BASELINE = Path(__file__).resolve().parents[1] / "reports/warehouse/agent_e2e_evaluation_v1.json"


@unittest.skipUnless(os.getenv("KNOWLEDGE_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class CapabilityTraceLinkageTests(unittest.TestCase):
    def setUp(self):
        self.manager, self.owner = local_manager()
        self.agent = self.manager.agent
        self.cid = self.manager.create_conversation(self.owner)["conversation_id"]
        historical = json.loads(BASELINE.read_text())["specialists"]["cases"]
        self.baseline = {case["golden"]["id"]: case for case in historical}

    def execute(self, case_id):
        case = self.baseline[case_id]
        model_output = case["model_outputs"][0]["model_output"]
        self.manager.generate = lambda _: model_output
        answer = self.manager.process_message(self.owner, self.cid, case["golden"]["question"], uuid4(), today=TODAY)
        history = self.manager.get_query(self.owner, answer["query_id"])
        return answer, history

    def test_recorded_failure_and_fixed_replay(self):
        baseline = self.baseline["hybrid"]
        results = {}
        with reference_connection() as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '15s'")
            _, _, _, previous_failure = evaluate_capture(
                baseline["golden"], baseline["answer"], baseline["history"],
                baseline["model_outputs"], self.agent.service, pg)
            self.assertEqual(previous_failure, "sql_capability_trace_unlinked")
            for case_id, phase in (("hybrid", "execution_recheck"), ("multi", "preflight")):
                answer, history = self.execute(case_id)
                self.assertEqual(answer["status"], "ok", answer)
                case = self.baseline[case_id]
                scores, ods, actual, failure = evaluate_capture(
                    case["golden"], answer, history, case["model_outputs"], self.agent.service, pg)
                self.assertIsNone(failure, scores)
                records = answer["trace"]["capability_records"]
                self.assertEqual(records, history["capability_records"])
                self.assertEqual(records, answer["routes"])
                self.assertEqual(len(records), 4 if case_id == "hybrid" else 2)
                tools = [tool for tool in answer["trace"]["tools"] if "task_id" in tool]
                self.assertEqual(len(tools), 2)
                self.assertNotEqual(tools[0]["task_id"], tools[1]["task_id"])
                for tool in tools:
                    actual_route = capability_link(tool, records)
                    self.assertEqual(actual_route["capability_phase"], phase)
                    self.assertEqual(actual_route["metric_request"], tool["request"])
                    self.assertEqual(tool["plan_id"], answer["result"]["plan_id"])
                    self.assertEqual(tool["attempt_id"], answer["result"]["attempt_id"])
                    original_task = next(task for task in answer["result"]["subtasks"] if task["task_id"] == tool["task_id"])
                    self.assertEqual(original_task["request"], tool["request"])
                self.assertIsNone(capability_link(tools[0] | {
                    "capability_trace_id": tools[1]["capability_trace_id"],
                }, records))
                results[case_id] = {"scores": scores, "ods": ods, "actual": actual, "trace_id": history["trace_id"],
                                    "trace": answer["trace"], "history": history}
        if path := os.getenv("CAPABILITY_LINK_REPORT"):
            Path(path).write_text(json.dumps({
                "mode": "captured_proposal_replay_postgres", "external_model_calls": 0,
                "baseline_report": BASELINE.name, "baseline_failure": previous_failure,
                "baseline_hybrid": {"routes": baseline["answer"]["routes"], "tools": baseline["answer"]["trace"]["tools"]},
                "replay": results,
            }, default=str, ensure_ascii=False, indent=2) + "\n")

    def test_recheck_denied_and_cross_task_id_never_execute_affected_sql(self):
        original = self.agent.capability.resolve
        query = self.agent.service.query

        for change, error_code in (("deny", "data_tool_not_authorized"), ("cross", "invalid_capability_trace_link")):
            with self.subTest(change=change):
                resolved = []

                def resolve(request):
                    route = original(request)
                    resolved.append(route)
                    if len(resolved) == 3:
                        if change == "deny":
                            return dict(route, status="UNSUPPORTED", reason_code="permission_denied")
                        return dict(route, trace_id=resolved[1]["trace_id"])
                    return route

                with patch.object(self.agent.capability, "resolve", side_effect=resolve), \
                        patch.object(self.agent.service, "query", wraps=query) as execute:
                    answer, history = self.execute("hybrid")
                self.assertEqual(answer["status"], "partial", answer)
                self.assertEqual([call.args[0]["metric_id"] for call in execute.call_args_list], ["order_item_count"])
                first, second, skipped, catalog = history["subtasks"]
                self.assertEqual(first["error_code"], error_code)
                self.assertEqual(skipped["error_code"], "dependency_failed")
                self.assertEqual((second["status"], catalog["status"]), ("ok", "ok"))
                self.assertEqual(history["capability_records"][2]["capability_phase"], "execution_recheck")
