"""Date-only equivalence proof, unchanged business validation, and real ODS reconciliation."""
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
from secrets import token_hex
import unittest
from unittest.mock import Mock
from uuid import uuid4

from dataquery_agent.business_plan import PlanError
from dataquery_agent.business_query_planner import BusinessQueryPlanner
from dataquery_agent.intent_time import normalize_explicit_windows
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from test_api import AUTH, TOKEN, client

FROZEN = json.loads((ROOT/'tests/date_window_cases_v1.json').read_text())
TODAY = date(2025,3,10)
BOUNDARY_QUESTIONS = [FROZEN['question'].replace('按品类',f'（{text}）按品类') for text in (
    '结束日不包含', '不计算最后一天', '起始日不包含', '不计算第一天',
    '包含结束日但不计算最后一天')]
BOUNDARY_QUESTIONS += [
    '不计算最后一天；'+FROZEN['question'],
    FROZEN['question']+'结束日不包含。',
    FROZEN['question'].replace('至2025-06-30','（不计算第一天）至2025-06-30')]


def retained_window(question):
    return question.replace('按品类同时列折扣前商品记录金额、商品行折扣金额；分别保留两列。','')


class ExplicitDateTests(unittest.TestCase):
    def setUp(self):
        self.question = FROZEN['question']
        self.proposal = deepcopy(FROZEN['recorded_proposal'])
        self.service = MetricQueryService(None)
        self.service.query = Mock(side_effect=AssertionError('must not execute SQL during planning'))
        self.planner = BusinessQueryPlanner(self.service)

    def test_proof_preserves_raw_values_and_original_validator(self):
        original = deepcopy(self.proposal)
        result = self.planner.prepare(self.question,lambda _:json.dumps(self.proposal),TODAY)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['plan']['time_windows'],[{'start_date':'2025-04-01','end_date':'2025-07-01'}])
        self.assertEqual(result['plan']['group_by'],['category'])
        self.assertEqual(result['plan']['metric_ids'],FROZEN['expected']['metric_ids'])
        event = result['date_window_validation']
        self.assertEqual(event['status'],'normalized')
        self.assertEqual(event['original_proposal'],original)
        self.assertEqual(event['source_boundaries'],event['model_boundaries'])
        self.assertEqual(self.proposal,original)
        with self.assertRaises(PlanError) as caught:
            self.planner.validator.validate(self.question,original,TODAY)
        self.assertEqual(caught.exception.response['code'],'window_mismatch')
        self.service.query.assert_not_called()

    def test_equivalent_separator_and_prefix_variants_only_for_explicit_ranges(self):
        for value in ('从2025-04-01至2025-06-30','2025-04-01 到 2025-06-30','2025-04-01～2025-06-30'):
            proposal = dict(self.proposal,time_windows=[value])
            result = self.planner.prepare(self.question,lambda _:json.dumps(proposal),TODAY)
            self.assertEqual(result['status'],'ready',result)
        proposal, event = normalize_explicit_windows('2025年4月订单量',dict(self.proposal,time_windows=['2025-04-01至2025-04-30']),TODAY)
        self.assertEqual(event['status'],'not_applicable')

    def test_changed_missing_or_unknown_boundaries_are_rejected(self):
        for name, windows in FROZEN['negative_windows'].items():
            with self.subTest(name=name):
                proposal = dict(self.proposal,time_windows=windows)
                result = self.planner.prepare(self.question,lambda _:json.dumps(proposal),TODAY)
                self.assertEqual(result['status'],'clarify',result)
                self.assertEqual(result['date_window_validation']['status'],'rejected')
        for question in (self.question.replace('按品类','（不含结束日）按品类'),
                         self.question.replace('按品类','（不含首日）按品类'),
                         self.question.replace('按品类','（排除最后一天）按品类'),
                         self.question.replace('从2025-04-01至2025-06-30','[2025-04-01,2025-06-30)')):
            proposal = dict(self.proposal,time_windows=['从2025-04-01至2025-06-30'])
            result = self.planner.prepare(question,lambda _:json.dumps(proposal),TODAY)
            self.assertEqual(result['code'],'unsupported_date_boundary')
        self.service.query.assert_not_called()

    def test_category_and_metric_validation_are_not_normalized(self):
        for change in ({'group_by':['按渠道']},{'metric_ids':['order_count']},
                       {'time_windows':['2025-04-01至2025-06-30','2025-05-01至2025-06-30']}):
            proposal = self.proposal | change
            result = self.planner.prepare(self.question,lambda _:json.dumps(proposal),TODAY)
            self.assertNotEqual(result['status'],'ready')
        self.service.query.assert_not_called()

    def test_source_boundary_guard_does_not_depend_on_model_retention(self):
        for question in BOUNDARY_QUESTIONS:
            for windows in (self.proposal['time_windows'],[retained_window(question)]):
                proposal = self.proposal | {'time_windows':windows}
                result = self.planner.prepare(question,lambda _:json.dumps(proposal),TODAY)
                self.assertEqual(result['code'],'unsupported_date_boundary',result)
                self.assertEqual(result['status'],'clarify')
                self.assertEqual(result['date_window_validation']['boundary_modifier']['source'],'user_question')
                with self.assertRaises(PlanError) as caught:
                    self.planner.validator.windows(question,proposal,TODAY)
                self.assertEqual(caught.exception.response['code'],'unsupported_date_boundary')
        business_question = self.question+'不含退款。'
        result = self.planner.prepare(business_question,lambda _:json.dumps(self.proposal),TODAY)
        self.assertEqual(result['code'],'unsupported_filter',result)
        self.assertEqual(result['date_window_validation']['status'],'normalized')
        self.service.query.assert_not_called()


@unittest.skipUnless(os.getenv('CONVERSATION_POSTGRES_TESTS')=='1','Real PostgreSQL opt-in')
class ExplicitDatePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_each_category_and_each_metric_and_zero_sql_for_negatives(self):
        from dataquery_agent.api import create_app
        from dataquery_agent.conversation_manager import local_manager
        from dataquery_agent.conversation_store import owner_hash
        from evaluate_business_planning import reference_connection
        from decimal import Decimal
        manager,_ = local_manager()
        manager.agent.supervisor = False
        owner=token_hex(32)
        manager.agent.service.execute = Mock(wraps=manager.agent.service.execute)
        manager.generate = Mock()
        app=create_app(token=TOKEN,manager_factory=lambda:(manager,owner))
        records=[]
        try:
            async with client(app) as http:
                async def ask(proposal,question=FROZEN['question']):
                    manager.generate.return_value=json.dumps(proposal)
                    cid=(await http.post('/api/conversations',headers=AUTH)).json()['conversation_id']
                    return (await http.post('/api/chat',headers=AUTH,json={'conversation_id':cid,
                        'message_id':str(uuid4()),'question':question})).json()
                actual=await ask(FROZEN['recorded_proposal'])
                self.assertEqual(actual['status'],'completed',actual)
                self.assertEqual(manager.agent.service.execute.call_count,2)
                with reference_connection() as pg:
                    pg.execute('SET TRANSACTION READ ONLY')
                    expected=pg.execute('''SELECT p.category_id::text AS category,
                        sum(i.quantity*i.unit_price) AS gross,sum(i.discount_amount) AS discount
                        FROM ods.order_items i JOIN ods.orders o USING(order_id) JOIN ods.products p USING(product_id)
                        WHERE o.ordered_at >= %s AND o.ordered_at < %s GROUP BY p.category_id''',
                        ('2025-04-01','2025-07-01')).fetchall()
                by_category={r['bucket']:r['metrics'] for r in actual['rows']}
                self.assertEqual(set(by_category),{r['category'] for r in expected})
                for row in expected:
                    cells=by_category[row['category']]
                    self.assertEqual(list(cells),FROZEN['expected']['metric_ids'])
                    self.assertEqual(Decimal(cells['item_gross_sales_amount']['value']),row['gross'])
                    self.assertEqual(Decimal(cells['item_discount_amount']['value']),row['discount'])
                event=actual['trace']['routing']['date_window_validation']
                self.assertEqual(event['status'],'normalized')
                saved=(await http.get(f"/api/queries/{actual['query_id']}",headers=AUTH)).json()
                self.assertEqual(saved['trace'],actual['trace'])
                for name, windows in FROZEN['negative_windows'].items():
                    before=manager.agent.service.execute.call_count
                    denied=await ask(FROZEN['recorded_proposal'] | {'time_windows':windows})
                    self.assertEqual(denied['status'],'clarification_required',denied)
                    self.assertEqual(manager.agent.service.execute.call_count,before)
                    records.append({'case':name,'status':denied['status'],'reason':denied['trace']['routing']['date_window_validation']['reason'],'business_metric_sql':0})
                before=manager.agent.service.execute.call_count
                denied=await ask(FROZEN['recorded_proposal'] | {'time_windows':['从2025-04-01至2025-06-30']},
                                 FROZEN['question'].replace('按品类','（不含结束日）按品类'))
                self.assertEqual(denied['status'],'clarification_required')
                self.assertEqual(manager.agent.service.execute.call_count,before)
                records.append({'case':'user_exclusive_boundary','status':denied['status'],'business_metric_sql':0})
                for question in BOUNDARY_QUESTIONS:
                    for retained in (False,True):
                        proposal = FROZEN['recorded_proposal']
                        if retained:
                            proposal = proposal | {'time_windows':[retained_window(question)]}
                        before = manager.agent.service.execute.call_count
                        denied = await ask(proposal,question)
                        self.assertEqual(denied['status'],'clarification_required',denied)
                        self.assertEqual(manager.agent.service.execute.call_count,before)
                        event = denied['trace']['routing']['date_window_validation']
                        self.assertEqual(event['reason'],'unsupported_date_boundary')
                        saved = (await http.get(f"/api/queries/{denied['query_id']}",headers=AUTH)).json()
                        self.assertEqual(saved['trace'],denied['trace'])
                        records.append({'question':question,'model_retained_boundary':retained,
                            'date_trace':event,'status':denied['status'],'business_metric_sql':0})
                if destination:=os.getenv('DATE_WINDOW_REPORT'):
                    Path(destination).write_text(json.dumps({'mode':'fixed recorded proposal + real PostgreSQL + FastAPI',
                        'positive':actual,'expected_ods':expected,'category_count':len(expected),'checked_cells':2*len(expected),
                        'negative':records,'real_gemini_calls':0},ensure_ascii=False,default=str,indent=2)+'\n')
        finally:
            with manager.store.transaction() as pg:
                pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s',(owner_hash(owner),))
