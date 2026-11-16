import json
import os
import unittest
from secrets import token_hex
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx2

from dataquery_agent.api import MAX_REQUEST_BYTES, create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import ConversationError
from test_business_planner_v2 import PROPOSE, QUESTION

TOKEN = "a" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INTENT = {"metrics": ["订单量"], "time": "2025年1月", "groups": [], "filters": [], "type": "metric", "missing": []}


def client(app, *, address="127.0.0.1"):
    transport = httpx2.ASGITransport(app=app, client=(address, 1234), raise_app_exceptions=False)
    return httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_schema_and_limits(self):
        manager = Mock()
        factory = Mock(return_value=(manager, "server-owner"))
        app = create_app(token=TOKEN, manager_factory=factory)
        body = {"conversation_id": str(uuid4()), "message_id": str(uuid4()), "question": "订单量"}
        async with client(app) as http:
            self.assertEqual((await http.get("/api/health")).json(), {"status": "ok"})
            self.assertEqual((await http.post("/api/chat", json=body)).status_code, 401)
            self.assertEqual((await http.post("/api/chat", json=body | {"owner_id": "secret"}, headers=AUTH)).status_code, 422)
            self.assertEqual((await http.post("/api/chat", json=body | {"is_admin": True}, headers=AUTH)).status_code, 422)
            self.assertEqual((await http.post("/api/chat", json=body | {"question": "长" * 501}, headers=AUTH)).status_code, 422)
            self.assertEqual((await http.post("/api/chat", content=b" " * (MAX_REQUEST_BYTES + 1), headers=AUTH)).status_code, 413)
            self.assertEqual((await http.get("/api/health", headers={"Host": "example.invalid"})).status_code, 400)
        async with client(app, address="192.0.2.2") as http:
            self.assertEqual((await http.get("/api/health")).status_code, 403)
        manager.process_message.assert_not_called()

    async def test_status_projection_and_redaction(self):
        manager = Mock()
        app = create_app(token=TOKEN, manager_factory=lambda: (manager, "server-owner"))
        body = {"conversation_id": str(uuid4()), "message_id": str(uuid4()), "question": "订单量"}
        async with client(app) as http:
            for source, expected in (("ok", "completed"), ("partial", "partial"),
                                     ("clarify", "clarification_required"), ("error", "failed")):
                manager.process_message.return_value = {
                    "status": source, "code": "tool_execution_failed" if source == "error" else None,
                    "answer_zh": "密码: confidential", "trace": {"model_attempts": [{"prompt": "internal-prompt"}]},
                    "result": {"query_result": {"results": [{"bucket": "all", "value": "2.10", "email": "private"}] * 51}},
                }
                response = await http.post("/api/chat", json=body, headers=AUTH)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], expected)
                self.assertNotIn("confidential", response.text)
                self.assertNotIn("internal-prompt", response.text)
                self.assertNotIn("email", response.text)
                self.assertEqual(len(response.json()["rows"]), 50)
                self.assertTrue(response.json()["rows_truncated"])
            manager.get_query.side_effect = ConversationError("query_not_found")
            path = f"/api/queries/{uuid4()}"
            self.assertEqual((await http.get(path)).status_code, 401)
            self.assertEqual((await http.get(path, headers=AUTH)).status_code, 404)
            manager.process_message.side_effect = RuntimeError("database credential")
            escaped = await http.post("/api/chat", json=body, headers=AUTH)
            self.assertEqual(escaped.status_code, 500)
            self.assertNotIn("credential", escaped.text)


@unittest.skipUnless(os.getenv("API_POSTGRES_TESTS") == "1", "PostgreSQL opt-in")
class ApiPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_queries_replay_trace_and_owner_isolation(self):
        manager, _ = local_manager()
        owner = token_hex(32)

        def generate(prompt):
            return json.dumps(PROPOSE if prompt.startswith("[business-plan-v1]") else INTENT)

        manager.generate = generate
        app = create_app(token=TOKEN, manager_factory=lambda: (manager, owner))
        other = create_app(token=TOKEN, manager_factory=lambda: (manager, token_hex(32)))
        async with client(app) as http:
            created = await http.post("/api/conversations", headers=AUTH)
            self.assertEqual(created.status_code, 201, created.text)
            cid = created.json()["conversation_id"]

            async def ask(question):
                body = {"conversation_id": cid, "message_id": str(uuid4()), "question": question}
                response = await http.post("/api/chat", json=body, headers=AUTH)
                self.assertEqual(response.status_code, 200, response.text)
                return body, response.json()

            _, single = await ask("2025年1月订单量是多少？")
            self.assertEqual(single["status"], "completed", single)
            self.assertEqual(single["rows"][0]["value"], "2312")
            body, hybrid = await ask(QUESTION)
            self.assertEqual(hybrid["status"], "completed", hybrid)
            self.assertEqual(hybrid["rows"][0]["metrics"]["order_item_count"]["value"], "5538")
            self.assertEqual(len(hybrid["citations"]), 2)
            self.assertEqual(len(hybrid["trace"]["tasks"]), 4)
            self.assertIn("plan_id", hybrid["trace"])
            self.assertIn("attempt_id", hybrid["trace"])
            history_path = f"/api/queries/{hybrid['query_id']}"
            history = (await http.get(history_path, headers=AUTH)).json()
            self.assertEqual(history["trace"]["capability"], hybrid["trace"]["capability"])
            self.assertEqual(history["trace"]["tasks"], hybrid["trace"]["tasks"])
            self.assertEqual(history["citations"], hybrid["citations"])
            self.assertEqual(history["status"], "completed")
            replay = (await http.post("/api/chat", json=body, headers=AUTH)).json()
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["query_id"], hybrid["query_id"])
            _, clarify = await ask("2025年1月销售额")
            self.assertEqual(clarify["status"], "clarification_required")
            self.assertTrue(clarify["clarification_choices"])

            with patch.object(manager.agent.knowledge.retriever.store, "catalog_chunks", return_value=[]):
                _, partial = await ask(QUESTION)
            self.assertEqual(partial["status"], "partial")
            self.assertEqual(len(partial["rows"]), 1)

            with patch.object(manager.agent.service, "query", return_value={
                "status": "error", "error": {"code": "query_timeout"},
            }):
                _, failed = await ask("2025年1月订单量是多少？")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"]["code"], "query_timeout")
        async with client(other) as http:
            self.assertEqual((await http.get(history_path, headers=AUTH)).status_code, 404)
