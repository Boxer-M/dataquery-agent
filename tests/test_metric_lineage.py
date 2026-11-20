"""SQL-backed evidence tests; real Neo4j is an explicit local opt-in."""
from copy import deepcopy
import json
import os
from pathlib import Path
from secrets import token_hex
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlglot import exp, parse_one

from dataquery_agent.lineage_evidence import FIELD, METRICS, LineageError, bundle, reviewed_paths
from dataquery_agent.metric_lineage import LineageTool, Neo4jLocal, route_lineage
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.unified_agent import UnifiedAgent
from test_metadata_capability import fixture_snapshot
from test_api import AUTH, TOKEN, client


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.data=bundle()
        self.rows=reviewed_paths(self.data,FIELD,['VALUE','DELETE'])
        self.backend=Mock()
        self.backend.query.return_value=deepcopy(self.rows)
        self.tool=LineageTool(self.backend)
        snapshot=fixture_snapshot()
        snapshot.relations['ods.order_items']={'name':'ods.order_items','kind':'r','rls':False,
            'schema_usage':True,'metric_schema_usage':False,'constraints':[],
            'columns':{'discount_amount':{'type':'numeric(12,2)','nullable':False,
                                         'readable':True,'metric_readable':False}}}
        self.agent=UnifiedAgent(MetricQueryService(None),CapabilityResolver(CapabilityRegistry(snapshot)))

    def test_sql_expressions_and_noncausal_catalog_candidate(self):
        self.assertEqual((len(self.data['nodes']),len(self.data['edges'])),(11,12))
        values={r['metric_id'] for r in self.rows if r['impact_type']=='VALUE'}
        self.assertEqual(values,{'item_net_sales_amount','item_discount_amount'})
        self.assertEqual({r['metric_id'] for r in self.rows if r['impact_type']=='DELETE'},set(METRICS))
        catalog=self.agent.service.metrics['order_item_count']
        self.assertIn(FIELD,catalog['source_fields'])
        tree=parse_one((ROOT/'sql/warehouse/transform/dws/product_sales_day.sql').read_text(),read='postgres')
        count=next(e for e in tree.selects if e.alias_or_name=='order_item_count')
        self.assertIsInstance(count.this,exp.Count)
        self.assertIsInstance(count.this.this,exp.Star)
        self.assertFalse(list(count.find_all(exp.Column)))
        for edge in self.data['edges']:
            self.assertTrue(edge['source_commit'])
            self.assertEqual(edge['source_version'],self.data['version'])
            self.assertGreaterEqual(edge['line_start'],1)
            self.assertGreaterEqual(edge['line_end'],edge['line_start'])
            self.assertTrue(edge['expression'])
        net=next(r for r in self.rows if r['metric_id']=='item_net_sales_amount' and r['impact_type']=='VALUE')
        self.assertEqual(net['path'],[FIELD,'dwd.order_item_fact.net_item_amount',
            'dws.product_sales_day.item_net_sales_amount','item_net_sales_amount'])
        self.assertIn('i.quantity * i.unit_price - i.discount_amount',net['evidence'][0]['expression'])

    def test_parameter_whitelist_and_evidence_integrity(self):
        self.assertEqual(self.tool.lookup(FIELD)['status'],'ok')
        self.assertEqual(self.backend.query.call_args.args[0],'impact')
        self.assertEqual(self.backend.query.call_args.args[1]['field'],FIELD)
        self.backend.reset_mock()
        self.assertEqual(self.tool.lookup(FIELD+"') DETACH DELETE n")['code'],'invalid_lineage_request')
        self.backend.query.assert_not_called()
        self.assertEqual(self.tool.lookup('dwd.order_item_fact.discount_amount')['code'],'field_not_indexed')
        self.backend.query.assert_not_called()
        for case in ('hash','version','path','missing'):
            rows=deepcopy(self.rows)
            if case=='hash': rows[0]['evidence'][0]['file_sha256']='0'*64
            if case=='version': rows[0]['evidence'][0]['source_version']='old'
            if case=='path': rows[0]['path'][1]='fake'
            if case=='missing': rows.pop()
            self.backend.query.return_value=rows
            with self.assertRaises(LineageError):self.tool.lookup(FIELD)
        self.backend.query.return_value=[]
        self.assertEqual(self.tool.lookup(FIELD)['code'],'lineage_index_missing')
        neo=Neo4jLocal('http://127.0.0.1:7474','neo4j/local-test-placeholder')
        with self.assertRaises(LineageError):neo.query('import',{},write=False)
        with self.assertRaises(LineageError):Neo4jLocal('http://example.invalid','neo4j/test')

    def test_changed_source_lock_is_rejected(self):
        manifest=json.loads((ROOT/'warehouse/lineage_discount_v1.json').read_text())
        manifest['reviewed_inputs']['metrics/warehouse_catalog_v1.json']='0'*64
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json';path.write_text(json.dumps(manifest))
            with patch('dataquery_agent.lineage_evidence.MANIFEST',path),self.assertRaisesRegex(LineageError,'source_changed'):
                bundle()

    def test_agent_unavailable_unknown_and_metadata_permissions(self):
        model=Mock(side_effect=AssertionError('no model permitted'))
        self.assertIsNone(route_lineage('PDF文档中的折扣字段变更有什么影响？',self.agent.capability,{'tools':[]}))
        with patch('dataquery_agent.metric_lineage.LineageTool.local',return_value=self.tool):
            answer=self.agent.answer('折扣字段变更会影响哪些指标',model)
            self.assertEqual(answer['status'],'ok')
            self.assertEqual(answer['task'],'lineage')
            self.assertIn('字段删除',answer['answer_zh'])
            unknown=self.agent.answer('ods.order_items.no_such_field 字段变更会影响哪些指标',model)
            self.assertEqual(unknown['code'],'field_not_found')
            column=self.agent.capability.registry.snapshot.get('ods.order_items')['columns']['discount_amount']
            with patch.dict(column,readable=False):
                self.assertEqual(self.agent.answer('折扣字段变更会影响哪些指标',model)['code'],'lineage_access_denied')
        with patch('dataquery_agent.metric_lineage.LineageTool.local',side_effect=LineageError('lineage_unavailable')):
            self.assertEqual(self.agent.answer('折扣字段变更会影响哪些指标',model)['code'],'lineage_unavailable')
        model.assert_not_called()


@unittest.skipUnless(os.getenv('NEO4J_INTEGRATION_TESTS')=='1','Real Neo4j + PostgreSQL opt-in')
class LineagePostgresNeo4jTests(unittest.IsolatedAsyncioTestCase):
    async def test_twice_import_agent_history_and_zero_metric_sql(self):
        from dataquery_agent.api import create_app
        from dataquery_agent.conversation_manager import local_manager
        from dataquery_agent.conversation_store import owner_hash
        tool=LineageTool.local()
        first,second=tool.import_graph(),tool.import_graph()
        self.assertEqual(first,second)
        self.assertEqual((first['nodes'],first['relationships']),(11,12))
        actual=tool.lookup(FIELD)
        self.assertEqual(len(actual['impacts']),5)
        values={r['metric_id'] for r in actual['impacts'] if r['impact_type']=='VALUE'}
        self.assertNotIn('order_item_count',values)
        self.assertIn('item_net_sales_amount',values)
        self.assertEqual(len(tool.lookup(FIELD,impact='value')['impacts']),2)
        self.assertEqual(len(tool.lookup(FIELD,impact='delete')['impacts']),3)
        manager,_=local_manager()
        owner=token_hex(32)
        manager.generate=Mock(side_effect=AssertionError('no Gemini'))
        manager.agent.service.execute=Mock(side_effect=AssertionError('no metric SQL'))
        app=create_app(token=TOKEN,manager_factory=lambda:(manager,owner))
        try:
            async with client(app) as http:
                cid=(await http.post('/api/conversations',headers=AUTH)).json()['conversation_id']
                async def ask(question):
                    return await http.post('/api/chat',headers=AUTH,json={'conversation_id':cid,
                        'message_id':str(uuid4()),'question':question})
                response=await ask('折扣字段变更会影响哪些指标')
                result=response.json()
                self.assertEqual(result['status'],'completed',result)
                self.assertIn('order_item_count',result['answer'])
                saved=(await http.get('/api/queries/'+result['query_id'],headers=AUTH)).json()
                self.assertEqual(saved['trace'],result['trace'])
                self.assertEqual((await ask('ods.order_items.no_such_field 字段变更会影响哪些指标')).json()['error']['code'],'field_not_found')
                with patch('dataquery_agent.metric_lineage.LineageTool.local',return_value=LineageTool(
                        Neo4jLocal('http://127.0.0.1:7474','neo4j/local-test-placeholder'))):
                    # A real rejected HTTP authentication, without stopping another local service.
                    unavailable=(await ask('折扣字段变更会影响哪些指标')).json()
                    self.assertEqual(unavailable['status'],'failed')
                    self.assertEqual(unavailable['error']['code'],'lineage_unavailable')
                self.assertEqual((await http.post('/api/chat',json={'conversation_id':cid,'message_id':str(uuid4()),
                    'question':'折扣字段变更会影响哪些指标'})).status_code,401)
                manager.generate.assert_not_called();manager.agent.service.execute.assert_not_called()
                if destination:=os.getenv('LINEAGE_REPORT'):
                    Path(destination).write_text(json.dumps({'mode':'real Neo4j and PostgreSQL History through HTTP ASGI',
                        'first_import':first,'second_import':second,'graph_result':actual,'http_response':result,
                        'history_trace_equal':True,'real_model_calls':0,'metric_sql':0,
                        'unknown_field':'field_not_found','real_http_auth_failure':unavailable['error']},ensure_ascii=False,indent=2)+'\n')
        finally:
            with manager.store.transaction() as pg:
                pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s',(owner_hash(owner),))
