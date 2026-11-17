"""Check live Redis and SQL Shed through the authenticated FastAPI entry point."""
import asyncio
import json
import os
from secrets import token_hex
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.performance import Performance
from test_api import AUTH, INTENT, TOKEN, client


@skipUnless(os.getenv("REDIS_POSTGRES_TESTS") == "1", "Requires local Redis and PostgreSQL")
class RedisTests(IsolatedAsyncioTestCase):
    async def test_cache_scopes_live_access_and_outage_fallback(self):
        manager, _ = local_manager()
        manager.generate = lambda _: json.dumps(INTENT)
        workload = Performance(url="redis://127.0.0.1:6381/0", data_version=uuid4().hex, report=True)
        first, other = token_hex(32), token_hex(32)

        def app(owner):
            return create_app(token=TOKEN, manager_factory=lambda: (manager, owner), performance=workload)

        async def ask(http, conversation):
            return await http.post("/api/chat", headers=AUTH, json={"conversation_id": conversation,
                                   "message_id": str(uuid4()), "question": "2025年1月订单量是多少？"})

        async def new(http):
            return (await http.post("/api/conversations", headers=AUTH)).json()["conversation_id"]

        async with client(app(first)) as http, client(app(other)) as stranger:
            cid = await new(http)
            cold = await ask(http, cid)
            hot = await ask(http, cid)
            self.assertEqual(cold.json()["status"], "completed", cold.text)
            self.assertEqual(cold.headers["x-dataquery-sql"], "1")
            self.assertEqual(hot.json()["rows"], cold.json()["rows"])
            self.assertEqual(hot.headers["x-dataquery-hits"], "1")
            self.assertEqual(hot.headers["x-dataquery-sql"], "0")
            self.assertEqual(hot.headers["x-dataquery-acl"], "1")
            self.assertEqual(hot.json()["trace"]["tools"][0]["cache"], "hit")
            self.assertNotEqual(hot.json()["trace"]["tools"][0]["trace_id"],
                                cold.json()["trace"]["tools"][0]["trace_id"])
            saved = await http.get(f"/api/queries/{hot.json()['query_id']}", headers=AUTH)
            self.assertEqual(saved.json()["trace"]["tools"], hot.json()["trace"]["tools"])
            self.assertEqual((await ask(stranger, cid)).status_code, 404)
            self.assertEqual((await ask(stranger, await new(stranger))).headers["x-dataquery-sql"], "1")
            self.assertFalse(manager.agent.service.cache_read_allowed({"ods.orders": ["order_id"]}))

            with patch.object(manager.agent.service, "cache_read_allowed", return_value=False):
                denied = await ask(http, cid)
                self.assertEqual(denied.json()["error"]["code"], "cache_access_denied")
                self.assertEqual(denied.json()["rows"], [])
                self.assertEqual(denied.headers["x-dataquery-sql"], "0")
            with patch.dict(manager.agent.service.catalog, {"catalog_version": "changed-test-catalog"}):
                version = await ask(http, cid)
                self.assertEqual(version.headers["x-dataquery-sql"], "1")
                self.assertEqual(version.headers["x-dataquery-hits"], "0")
            workload.data_version = uuid4().hex
            self.assertEqual((await ask(http, cid)).headers["x-dataquery-sql"], "1")

            workload.data_version = uuid4().hex
            peers = [await new(http) for _ in range(5)]
            simultaneous = await asyncio.gather(*(ask(http, peer) for peer in peers))
            self.assertEqual(sum(int(item.headers["x-dataquery-sql"]) for item in simultaneous), 1)
            self.assertTrue(all(item.json()["status"] == "completed" or
                                item.json()["error"]["code"] == "cache_fill_busy" for item in simultaneous))

            down = Performance(url="redis://127.0.0.1:1/0", data_version=uuid4().hex, limit=1, report=True)
            fallback = create_app(token=TOKEN, manager_factory=lambda: (manager, first), performance=down)
            async with client(fallback) as offline:
                completed = await ask(offline, cid)
                self.assertEqual(completed.json()["rows"], cold.json()["rows"])
                self.assertEqual(completed.headers["x-dataquery-sql"], "1")
                limited = await ask(offline, cid)
                self.assertEqual(limited.status_code, 429)
                self.assertGreater(int(limited.headers["Retry-After"]), 0)
