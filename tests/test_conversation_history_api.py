"""Owner-scoped browsing and display snapshots; never replay business execution."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from secrets import token_hex
import unittest
from unittest.mock import Mock
from uuid import uuid4

from dataquery_agent.api import create_app
from dataquery_agent.api_models import public_response
from dataquery_agent.conversation_store import ConversationError, owner_hash
from test_api import AUTH, TOKEN, INTENT, client
from test_artifact_api import completed_record


class ConversationHistoryApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_bounded_pages_and_old_snapshot(self):
        manager = Mock()
        cid, qid, mid = uuid4(), uuid4(), uuid4()
        row = {'conversation_id': cid, 'title': '密码: private', 'updated_at': datetime.now(timezone.utc)}
        manager.store.list_conversations.return_value = [row] * 21
        manager.store.list_messages.return_value = [dict(query_id=qid, message_id=mid,
            started_at=datetime.now(timezone.utc), question='2025年1月订单量是多少？')]
        app = create_app(token=TOKEN, manager_factory=lambda: (manager, 'server-owner'))
        async with client(app) as http:
            self.assertEqual((await http.get('/api/conversations')).status_code, 401)
            response = await http.get('/api/conversations?offset=20', headers=AUTH)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()['conversations']), 20)
            self.assertTrue(response.json()['has_more'])
            self.assertNotIn('private', response.text)
            manager.store.list_conversations.assert_called_once_with('server-owner', 20)
            path = f'/api/conversations/{cid}/messages'
            self.assertEqual((await http.get(path, headers=AUTH)).json()['messages'][0]['query_id'], str(qid))
            manager.store.list_messages.side_effect = ConversationError('conversation_not_found')
            self.assertEqual((await http.get(path, headers=AUTH)).status_code, 404)
            self.assertEqual((await http.get('/api/conversations?offset=-1', headers=AUTH)).status_code, 422)
        manager.generate.assert_not_called()
        manager.agent.service.query.assert_not_called()
        record = completed_record(qid)
        saved = public_response(record, history=True)
        self.assertEqual(saved.rows, record['display_snapshot']['rows'])
        self.assertIsNotNone(saved.answer)
        old = deepcopy(record)
        old.pop('display_snapshot')
        self.assertEqual(public_response(old, history=True).query_id, qid)


@unittest.skipUnless(os.getenv('CONVERSATION_POSTGRES_TESTS') == '1', 'Real PostgreSQL opt-in')
class ConversationHistoryPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_browse_restart_owner_isolation_and_zero_execution(self):
        from dataquery_agent.conversation_manager import local_manager
        from dataquery_agent.conversation_store import ConversationStore
        manager, _ = local_manager()
        manager.agent.supervisor = False
        manager.generate = Mock(return_value=json.dumps(INTENT))
        manager.agent.service.query = Mock(wraps=manager.agent.service.query)
        a, b = token_hex(32), token_hex(32)
        owner = [a]
        app = create_app(token=TOKEN, manager_factory=lambda: (manager, owner[0]))
        try:
            async with client(app) as http:
                cid = (await http.post('/api/conversations', headers=AUTH)).json()['conversation_id']
                initial = await http.post('/api/chat', headers=AUTH, json=dict(conversation_id=cid,
                    message_id=str(uuid4()), question='2025年1月订单量是多少？'))
                self.assertEqual(initial.json()['status'], 'completed', initial.text)
                self.assertEqual(initial.json()['rows'][0]['value'], '2312')
                newer = (await http.post('/api/conversations', headers=AUTH)).json()['conversation_id']
                calls = manager.agent.service.query.call_count, manager.generate.call_count
                manager.store = ConversationStore(manager.store.dsn)  # No browser memory or live result required.
                sessions = (await http.get('/api/conversations', headers=AUTH)).json()['conversations']
                self.assertEqual({row['conversation_id'] for row in sessions}, {cid, newer})
                page = (await http.get(f'/api/conversations/{cid}/messages', headers=AUTH)).json()
                self.assertEqual(len(page['messages']), 1)
                self.assertEqual(page['messages'][0]['question'], '2025年1月订单量是多少？')
                qid = page['messages'][0]['query_id']
                restored = await http.get(f'/api/queries/{qid}', headers=AUTH)
                self.assertEqual(restored.json()['rows'], initial.json()['rows'])
                self.assertEqual(restored.json()['answer'], initial.json()['answer'])
                owner[0] = b
                self.assertEqual((await http.get('/api/conversations', headers=AUTH)).json()['conversations'], [])
                self.assertEqual((await http.get(f'/api/conversations/{cid}/messages', headers=AUTH)).status_code, 404)
                self.assertEqual((await http.get(f'/api/queries/{qid}', headers=AUTH)).status_code, 404)
                self.assertEqual((manager.agent.service.query.call_count, manager.generate.call_count), calls)
        finally:
            with manager.store.transaction() as pg:
                pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=ANY(%s)',
                           ([owner_hash(a), owner_hash(b)],))
