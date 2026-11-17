"""Exercise actual PostgreSQL checkpoints across application lifetimes."""
from datetime import datetime, timedelta, timezone
import json
import os
from secrets import token_hex
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

from psycopg.types.json import Jsonb

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import owner_hash
from test_api import AUTH, TOKEN, client


@skipUnless(os.getenv("HITL_POSTGRES_TESTS") == "1", "Requires provisioned HITL PostgreSQL")
class HitlTests(IsolatedAsyncioTestCase):
    async def test_pause_restart_resume_and_access_controls(self):
        owner, other = token_hex(32), token_hex(32)
        first, _ = local_manager()
        first.agent.supervisor = True
        if os.getenv("HITL_LIVE_GEMINI") != "1":
            first.generate = lambda _: json.dumps({"decision": "clarify"})

        with patch.dict(os.environ, {"DATAQUERY_HITL": "1"}):
            async with client(create_app(token=TOKEN, manager_factory=lambda: (first, owner))) as http:
                conversation = (await http.post("/api/conversations", headers=AUTH)).json()["conversation_id"]

                async def pause():
                    message = {"message_id": str(uuid4()), "conversation_id": conversation,
                               "question": "2025年1月销售额"}
                    response = await http.post("/api/chat", headers=AUTH, json=message)
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertEqual(result["hitl"]["status"], "paused", result)
                    self.assertEqual(result["trace"]["tools"], [])
                    return result

                paused, revoked, expired, permission = [await pause() for _ in range(4)]

        restarted, _ = local_manager()
        body = {"metric_id": "item_net_sales_amount"}
        url = f"/api/queries/{paused['query_id']}"
        async with client(create_app(token=TOKEN, manager_factory=lambda: (restarted, other))) as stranger:
            self.assertEqual((await stranger.post(url + "/resume", headers=AUTH, json=body)).status_code, 404)

        async with client(create_app(token=TOKEN, manager_factory=lambda: (restarted, owner))) as http:
            restored = (await http.get(url, headers=AUTH)).json()
            self.assertEqual(restored["hitl"], paused["hitl"])
            with patch.object(restarted.agent.service, "query", wraps=restarted.agent.service.query) as query:
                self.assertEqual((await http.post(url + "/resume", headers=AUTH,
                                                  json={"metric_id": "order_count"})).status_code, 409)
                finished = await http.post(url + "/resume", headers=AUTH, json=body)
                self.assertEqual(finished.status_code, 200, finished.text)
                final = finished.json()
                self.assertEqual(final["hitl"]["status"], "completed", final)
                self.assertEqual(final["query_id"], paused["query_id"])
                self.assertEqual(final["trace"]["trace_id"], paused["trace"]["trace_id"])
                self.assertEqual(final["trace"]["run_id"], paused["trace"]["run_id"])
                self.assertTrue(final["rows"][0]["value"])
                self.assertEqual((await http.post(url + "/resume", headers=AUTH, json=body)).status_code, 409)
                self.assertEqual(query.call_count, 1)
            saved = (await http.get(url, headers=AUTH)).json()
            self.assertEqual(saved["trace"], final["trace"])
            self.assertEqual(saved["hitl"], final["hitl"])

            with restarted.store.transaction() as pg:
                pg.execute("UPDATE agent_meta.conversations SET owner_hash=%s WHERE conversation_id=%s",
                           (owner_hash(other), conversation))
            try:
                denied = await http.post(f"/api/queries/{revoked['query_id']}/resume", headers=AUTH, json=body)
                self.assertEqual(denied.status_code, 404)
                self.assertNotIn("rows", denied.json())
            finally:
                with restarted.store.transaction() as pg:
                    pg.execute("UPDATE agent_meta.conversations SET owner_hash=%s WHERE conversation_id=%s",
                               (owner_hash(owner), conversation))
            record = restarted.get_query(owner, expired["query_id"])
            record["hitl"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            for key in ("started_at", "completed_at"):
                record.pop(key)
            with restarted.store.transaction() as pg:
                pg.execute("UPDATE agent_meta.query_history SET record=%s WHERE query_id=%s",
                           (Jsonb(record), expired["query_id"]))
            expired_url = f"/api/queries/{expired['query_id']}"
            self.assertEqual((await http.get(expired_url, headers=AUTH)).json()["hitl"]["status"], "expired")
            self.assertEqual((await http.post(expired_url + "/resume", headers=AUTH, json=body)).status_code, 409)

            with patch.object(restarted.agent.capability, "resolve", return_value={
                "status": "UNSUPPORTED", "reason_code": "permission_revoked",
            }), patch.object(restarted.agent.service, "query") as denied_query:
                response = await http.post(f"/api/queries/{permission['query_id']}/resume", headers=AUTH, json=body)
                self.assertEqual(response.json()["hitl"]["status"], "failed")
                self.assertEqual(response.json()["rows"], [])
                denied_query.assert_not_called()
