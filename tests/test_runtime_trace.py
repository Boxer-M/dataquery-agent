import unittest
from uuid import uuid4

from dataquery_agent.api_models import public_response
from dataquery_agent.query_history import build_record, replay_summary


class RuntimeTraceTests(unittest.TestCase):
    def test_shared_projection_preserves_events_and_legacy_replay(self):
        claim = {key: uuid4() for key in ("conversation_id", "message_id")}
        query_id = uuid4()
        answer = {"status": "ok", "trace": {"trace_id": "run-trace", "tools": [
            {"trace_id": "tool-1", "status": "ok", "strategy": "catalog_exact",
             "agent_call": {"agent": "knowledge", "elapsed_ms": 2, "prompt": "internal"}},
            {"trace_id": "tool-2", "status": "skipped", "reason_code": "dependency_failed",
             "failed_dependencies": ["plan/1"]},
        ]}}
        record = build_record(claim, "问题", answer, "new", 5, query_id=query_id)
        live = public_response(dict(answer, query_id=query_id)).trace
        self.assertEqual(live, public_response(record, history=True).trace)
        self.assertEqual([step.trace_id for step in live.tools], ["tool-1", "tool-2"])
        self.assertEqual(live.tools[0].agent_elapsed_ms, 2)
        self.assertEqual(live.tools[1].failed_dependencies, ["plan/1"])
        self.assertNotIn("prompt", record["tool_traces"][0]["agent_call"])

        old = {"query_id": str(query_id), "trace_id": "old-trace", "execution_status": "ok",
               "result_summary": {}, "tool_traces": [{"trace_id": "old-tool", "status": "ok"}]}
        replay = public_response(dict(replay_summary(old, answer), replayed=True)).trace
        self.assertEqual((replay.run_id, replay.trace_id), (query_id, "old-trace"))
        self.assertEqual(public_response(old, history=True).trace.tools[0].trace_id, "old-tool")
