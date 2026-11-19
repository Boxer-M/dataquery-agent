"""Explicit backend exports; fixed input tests separate from live PostgreSQL."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from openpyxl import load_workbook

from dataquery_agent.api import create_app
from dataquery_agent.artifact_api import XLSX
from dataquery_agent.artifact_store import ArtifactStore
from dataquery_agent.conversation_store import ConversationError
from dataquery_agent.query_history import build_record, export_snapshot
from test_api import AUTH, TOKEN, client


def completed_record(query_id):
    from test_unified_agent import UnifiedAgentTests, CASES, TODAY
    from test_skill_business import QUESTION, model_for
    fixture = UnifiedAgentTests()
    fixture.setUp()
    fixture.service.query.side_effect = lambda request: fixture.fake_query(CASES['multi'], request)
    answer = fixture.agent.answer(QUESTION, model_for(usage=False), today=TODAY)
    claim = {'conversation_id': str(uuid4()), 'message_id': str(uuid4())}
    return build_record(claim, QUESTION, answer, 'new', 1, query_id=query_id, metrics=fixture.service.metrics)


class ArtifactApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'artifacts'
        self.store = ArtifactStore(self.directory)
        self.a, self.b = token_hex(32), token_hex(32)
        self.owner = self.a
        self.qid = str(uuid4())
        self.record = completed_record(self.qid)
        self.manager = Mock()
        def history(owner, qid):
            if owner != self.a or str(qid) != self.qid:
                raise ConversationError('query_not_found')
            return deepcopy(self.record)
        self.manager.get_query.side_effect = history
        self.app = self.new_app(self.store)
        self.export_url = f'/api/queries/{self.qid}/artifacts/xlsx'

    def new_app(self, store):
        return create_app(token=TOKEN, manager_factory=lambda: (self.manager, self.owner), artifact_store=store)

    async def test_create_download_restart_and_owner_isolation(self):
        async with client(self.app) as http:
            created = await http.post(self.export_url, json={}, headers=AUTH)
            self.assertEqual(created.status_code, 201, created.text)
            metadata = created.json()
            url = f'/api/artifacts/{metadata["artifact_id"]}/download'
            downloaded = await http.get(url, headers=AUTH)
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.headers['content-type'], XLSX)
            self.assertIn(metadata['filename'], downloaded.headers['content-disposition'])
            stored = self.store.get(self.a, metadata['artifact_id'])
            self.assertEqual(stored['content'], downloaded.content)
            self.assertEqual(sha256(downloaded.content).hexdigest(), metadata['sha256'])
            self.assertNotIn('owner', metadata)
            book = load_workbook(BytesIO(downloaded.content), data_only=True)
            self.assertEqual(book.active['A2'].value, '订单量')
            book.close()
            self.owner = self.b
            self.assertEqual((await http.post(self.export_url, json={}, headers=AUTH)).status_code, 404)
            self.assertEqual((await http.get(url, headers=AUTH)).status_code, 404)
            self.owner = self.a
            self.assertEqual((await http.get(url)).status_code, 401)
            self.assertEqual((await http.post(self.export_url, json={})).status_code, 401)
            self.assertEqual((await http.get(f'/api/artifacts/{uuid4()}/download', headers=AUTH)).status_code, 404)
            self.assertEqual((await http.post(self.export_url, json={'owner_id': self.b}, headers=AUTH)).status_code, 422)
        async with client(self.new_app(ArtifactStore(self.directory))) as restarted:
            self.assertEqual((await restarted.get(url, headers=AUTH)).content, downloaded.content)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory / 'artifacts.sqlite3').stat().st_mode & 0o777, 0o600)

    async def test_legacy_partial_and_missing_source_are_not_requeried(self):
        async with client(self.app) as http:
            for change in ({'export_snapshot': None}, {'execution_status': 'partial'}):
                with patch.dict(self.record, change):
                    response = await http.post(self.export_url, json={}, headers=AUTH)
                    self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual((await http.post(f'/api/queries/{uuid4()}/artifacts/xlsx', json={}, headers=AUTH)).status_code, 404)
            created = (await http.post(self.export_url, json={}, headers=AUTH)).json()
            self.manager.get_query.side_effect = ConversationError('query_not_found')
            self.assertEqual((await http.get(f'/api/artifacts/{created["artifact_id"]}/download', headers=AUTH)).status_code, 404)
        self.manager.agent.service.query.assert_not_called()
        self.manager.generate.assert_not_called()

    async def test_insert_then_failure_rolls_back_blob_and_metadata(self):
        original = self.store._insert
        def failing_insert(db, record, content):
            original(db, record, content)
            raise OSError('private path not for HTTP')
        async with client(self.app) as http:
            with patch.object(self.store, '_insert', side_effect=failing_insert):
                response = await http.post(self.export_url, json={}, headers=AUTH)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private path', response.text)
            self.assertNotIn('artifact_id', response.json())
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM artifacts').fetchone()[0], 0)

    async def test_export_eligibility_matches_history_and_direct_api(self):
        from dataquery_agent.export_eligibility import xlsx_export
        from dataquery_agent.api_models import public_response
        original = deepcopy(self.record)
        async with client(self.app) as http:
            for name, groups, snapshot, code in (
                ('summary', [], True, 'xlsx_export_available'),
                ('month', ['month'], False, 'query_export_grouped'),
                ('channel', ['channel'], False, 'query_export_grouped'),
                ('one_group_row', ['channel'], True, 'query_export_grouped'),
                ('legacy', [], False, 'query_export_snapshot_missing'),
            ):
                with self.subTest(name=name):
                    self.record = deepcopy(original)
                    self.record['resolved_request']['group_by'] = groups
                    if not snapshot:
                        self.record.pop('export_snapshot')
                    eligibility = xlsx_export(self.record)[0]
                    self.assertEqual(eligibility['code'], code)
                    # Even an otherwise valid scalar snapshot cannot override grouping semantics.
                    live = public_response({'status': 'ok', 'xlsx_export': eligibility})
                    history = await http.get(f'/api/queries/{self.qid}', headers=AUTH)
                    self.assertEqual(history.json()['xlsx_export'], live.xlsx_export.model_dump())
                    created = await http.post(self.export_url, json={}, headers=AUTH)
                    self.assertEqual(created.status_code, 201 if name == 'summary' else 409)
                    if name != 'summary':
                        self.assertEqual(created.json()['error']['code'], code)
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM artifacts').fetchone()[0], 1)
        self.manager.agent.service.query.assert_not_called()
        self.manager.generate.assert_not_called()

    async def test_missing_blob_and_missing_storage_return_not_found(self):
        async with client(self.app) as http:
            metadata = (await http.post(self.export_url, json={}, headers=AUTH)).json()
            url = f'/api/artifacts/{metadata["artifact_id"]}/download'
            with self.store.connect(write=True) as db:
                db.execute('UPDATE artifacts SET content=?', (b'',))
            self.assertEqual((await http.get(url, headers=AUTH)).status_code, 404)
            (self.directory / 'artifacts.sqlite3').unlink()
            self.assertEqual((await http.get(url, headers=AUTH)).status_code, 404)

    def test_snapshot_is_bounded_and_keeps_original_catalog_names(self):
        snapshot = self.record['export_snapshot']
        self.assertEqual(set(snapshot), {'version', 'result', 'metrics'})
        self.assertEqual(snapshot['metrics']['order_count'], {'name_zh': '订单量'})
        self.assertNotIn('answer_zh', str(snapshot))
        single = {'status': 'ok', 'result': {'status': 'ok', 'query_result': {
            'status': 'ok', 'metric_id': 'order_count', 'start_date': '2025-01-01', 'end_date': '2025-02-01',
            'group_by': [], 'results': [{'bucket': 'all', 'value': '7'}]}}}
        self.assertEqual(export_snapshot(single, {'order_count': {'name_zh': '订单量'}})['result']['results'][0]['metrics'],
                         {'order_count': {'value': '7'}})


@unittest.skipUnless(os.getenv('KNOWLEDGE_POSTGRES_TESTS') == '1', 'Real PostgreSQL opt-in')
class ArtifactPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_export_download_and_persistence(self):
        from dataquery_agent.conversation_manager import local_manager
        from test_skill_business import QUESTION, model_for
        manager, a = local_manager()
        manager.agent.supervisor = False
        manager.generate = model_for(usage=False)
        identity = [a]
        statements = []
        with TemporaryDirectory() as directory:
            def storage():
                store = ArtifactStore(Path(directory) / 'reports')
                original = store.connect
                @contextmanager
                def counted(**kwargs):
                    with original(**kwargs) as db:
                        db.set_trace_callback(statements.append)
                        yield db
                store.connect = counted
                return store
            store = storage()
            def app(current):
                return create_app(token=TOKEN, manager_factory=lambda: (manager, identity[0]), artifact_store=current)
            with patch.object(manager.agent.service, 'query', wraps=manager.agent.service.query) as query, patch.object(
                    manager, 'get_query', wraps=manager.get_query) as history:
                async with client(app(store)) as http:
                    cid = (await http.post('/api/conversations', headers=AUTH)).json()['conversation_id']
                    chat = await http.post('/api/chat', json={'conversation_id': cid, 'message_id': str(uuid4()),
                                                            'question': QUESTION}, headers=AUTH)
                    self.assertEqual(chat.json()['status'], 'completed', chat.text)
                    self.assertTrue(chat.json()['xlsx_export']['eligible'])
                    qid = chat.json()['query_id']
                    saved = await http.get(f'/api/queries/{qid}', headers=AUTH)
                    self.assertEqual(saved.json()['xlsx_export'], chat.json()['xlsx_export'])
                    self.assertEqual(query.call_count, 2)
                    created = await http.post(f'/api/queries/{qid}/artifacts/xlsx', json={}, headers=AUTH)
                    self.assertEqual(created.status_code, 201, created.text)
                    metadata = created.json()
                    url = f'/api/artifacts/{metadata["artifact_id"]}/download'
                    downloaded = await http.get(url, headers=AUTH)
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.headers['content-type'], XLSX)
                    self.assertEqual(store.get(a, metadata['artifact_id'])['content'], downloaded.content)
                    book = load_workbook(BytesIO(downloaded.content), data_only=True)
                    self.assertEqual(list(book.active.values), [('指标', '数值'), ('订单量', 2312), ('成功支付金额', 230864.57)])
                    self.assertEqual(book.active['B3'].number_format, '0.00')
                    readback = {'order_count': str(book.active['B2'].value),
                                'captured_payment_amount': str(book.active['B3'].value),
                                'amount_format': book.active['B3'].number_format}
                    book.close()
                    identity[0] = token_hex(32)
                    self.assertEqual((await http.post(f'/api/queries/{qid}/artifacts/xlsx', json={}, headers=AUTH)).status_code, 404)
                    self.assertEqual((await http.get(url, headers=AUTH)).status_code, 404)
                    identity[0] = a
                async with client(app(storage())) as restarted:
                    self.assertEqual((await restarted.get(url, headers=AUTH)).content, downloaded.content)
                self.assertEqual(query.call_count, 2)
                self.assertEqual(manager.generate.call_count, 1)  # Initial fixed proposal only.
            if path := os.getenv('ARTIFACT_API_REPORT'):
                Path(path).write_text(json.dumps({
                    'mode': 'FastAPI ASGI HTTP + real PostgreSQL; fixed proposal; private SQLite BLOB',
                    'query_id': qid, 'artifact': metadata, 'download_sha256': sha256(downloaded.content).hexdigest(),
                    'readback': readback,
                    'same_bytes_after_reinitialization': True, 'cross_owner_export_and_download_denied': True,
                    'initial_business_metric_sql': query.call_count, 'export_download_business_metric_sql': 0,
                    'artifact_metadata_reads': sum(sql.lstrip().upper().startswith('SELECT') for sql in statements),
                    'artifact_metadata_writes': sum(sql.lstrip().upper().startswith('INSERT') for sql in statements),
                    'query_history_access_checks': history.call_count,
                    'real_gemini_calls': 0,
                }, ensure_ascii=False, indent=2) + '\n')
