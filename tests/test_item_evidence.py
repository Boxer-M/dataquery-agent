"""Offline subquestion isolation; no network, database or fabricated business results."""
from datetime import date
import json
from unittest.mock import Mock,patch
import unittest

from dataquery_agent.agent_runtime import AgentRuntime
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.item_evidence import split_pair
from dataquery_agent.runtime_trace import tool_event

QUESTION='根据已审核PDF，2025年1月web渠道的订单量和支付尝试次数分别是多少？'


def entry(i,quote=None):
    return {'id':f'item_{i}','status':'supported' if quote else 'insufficient',
            'claims':[{'chunk_id':f'chunk{i}','quote':quote}] if quote else []}


class ItemEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.evidence=[{'chunk_id':f'chunk{i}','evidence_text':text,'source_type':'reviewed_pdf',
            'title':'Offline PDF','page_number':i,'line_start':1,'line_end':1,'document_version':'v1','source_uri':'reviewed-pdf:offline'}
            for i,text in ((1,'order_count: 1337'),(2,'payment_attempt_count: 1408'))]
        self.retriever=Mock();self.retriever.search.return_value=([{}],{'channels':{}})
        self.retriever.store.current_pdf_citations.side_effect=lambda citations,**_:citations

    def run_model(self,items,*,question=QUESTION,raw=None):
        model=Mock(return_value=raw if raw is not None else json.dumps({'items':items}))
        runtime=AgentRuntime(model)
        with patch('dataquery_agent.rag.answer.build_evidence',return_value=(self.evidence,[],[])):
            result=KnowledgeService(self.retriever,{}).answer(question,runtime,today=date(2026,11,23),owner='owner',pdf_only=True)
        self.assertEqual(model.call_count,1)
        return result,runtime,model

    def test_two_supported_items_derive_supported(self):
        result,runtime,_=self.run_model([entry(2,'payment_attempt_count: 1408'),entry(1,'order_count: 1337')])
        self.assertEqual(result['evidence_status'],'supported')
        self.assertEqual([i['id'] for i in result['evidence_items']],['item_1','item_2'])
        self.assertEqual(len(result['citations']),2)
        self.assertEqual(runtime.attempts[0]['stage'],'knowledge_items')

    def test_one_supported_item_is_retained(self):
        result,_,_=self.run_model([entry(1,'order_count: 1337'),entry(2)])
        self.assertEqual(result['evidence_status'],'partial')
        self.assertIn('1337',result['answer_zh'])
        self.assertIn('支付尝试次数：\n证据不足',result['answer_zh'])
        self.assertEqual(len(result['citations']),1)

    def test_both_unsupported(self):
        result,_,_=self.run_model([entry(1),entry(2)])
        self.assertEqual(result['evidence_status'],'insufficient')
        self.assertFalse(result['citations'])

    def test_missing_child_preserves_other(self):
        result,_,_=self.run_model([entry(1,'order_count: 1337')])
        self.assertEqual(result['evidence_status'],'partial')
        self.assertEqual(result['evidence_items'][1]['reason_code'],'missing_item')

    def test_duplicate_child_does_not_choose_a_convenient_copy(self):
        result,_,_=self.run_model([entry(1,'order_count: 1337'),entry(1),entry(2,'payment_attempt_count: 1408')])
        self.assertEqual(result['evidence_status'],'partial')
        self.assertEqual(result['evidence_items'][0]['reason_code'],'duplicate_id')
        self.assertEqual(result['citations'][0]['quote'],'payment_attempt_count: 1408')

    def test_invalid_quote_only_invalidates_its_child(self):
        result,_,_=self.run_model([entry(1,'invented count 9999'),entry(2,'payment_attempt_count: 1408')])
        self.assertEqual(result['evidence_status'],'partial')
        self.assertEqual(result['evidence_items'][0]['reason_code'],'invalid_citation')
        self.assertNotIn('9999',result['answer_zh'])

    def test_invalid_child_shape_and_unknown_id_preserve_known_child(self):
        for bad in ({'id':'unknown','status':'supported','claims':[]},
                    {'id':'item_1','status':[],'claims':[]},entry(1)|{'answer':'unverified answer'}):
            result,_,_=self.run_model([bad,entry(2,'payment_attempt_count: 1408')])
            self.assertEqual(result['evidence_status'],'partial')
            self.assertEqual(len(result['citations']),1)

    def test_current_version_failure_isolated_by_item(self):
        self.retriever.store.current_pdf_citations.side_effect=lambda citations,**_:[c for c in citations if c['chunk_id']=='chunk1']
        result,_,_=self.run_model([entry(1,'order_count: 1337'),entry(2,'payment_attempt_count: 1408')])
        self.assertEqual(result['evidence_status'],'partial')
        self.assertEqual(result['evidence_items'][1]['reason_code'],'source_invalidated')
        self.assertEqual(result['evidence_items'][1]['citations'],[])
        self.assertIn('1337',result['answer_zh'])

    def test_whole_response_failure_uses_existing_safe_failure(self):
        for raw in ('not-json','{"items":null}','{"status":"supported","claims":[]}'):
            result,runtime,_=self.run_model([],raw=raw)
            self.assertEqual(result['evidence_status'],'insufficient')
            self.assertFalse(result['citations'])
            self.assertEqual(runtime.attempts[0]['status'],'invalid_output')

    def test_single_and_uncovered_forms_use_original_contract(self):
        for question in ('PDF订单量是多少？','根据已审核PDF，2025年1月web渠道订单量和2025年2月web渠道支付尝试次数分别是多少？'):
            self.assertEqual(split_pair(question),[])
            raw=json.dumps({'status':'supported','claims':[{'chunk_id':'chunk1','quote':'order_count: 1337'}]})
            result,runtime,_=self.run_model([],question=question,raw=raw)
            self.assertEqual(runtime.attempts[0]['stage'],'knowledge')
            self.assertNotIn('evidence_items',result)

    def test_no_model_when_no_active_evidence(self):
        model=Mock(side_effect=AssertionError('no model expected'))
        with patch('dataquery_agent.rag.answer.build_evidence',return_value=([],[],[])):
            result=KnowledgeService(self.retriever,{}).answer(QUESTION,AgentRuntime(model),today=date(2026,11,23),owner='owner',pdf_only=True)
        self.assertEqual(result['evidence_status'],'insufficient')
        self.assertEqual(len(result['evidence_items']),2)
        model.assert_not_called()

    def test_split_is_generic_and_trace_keeps_item_ids(self):
        questions=split_pair('根据已审核PDF，2026年2月partner渠道客户数与记录金额分别是多少？')
        self.assertEqual([q['label'] for q in questions],['客户数','记录金额'])
        self.assertEqual(split_pair('根据已审核PDF，2025年1月web渠道订单量和支付尝试次数和销售金额分别是多少？'),[])
        result,_,_=self.run_model([entry(1,'order_count: 1337'),entry(2)])
        audit=tool_event(result['retrieval_trace'])['retrieval']['item_evidence']
        self.assertEqual(audit['supported_count'],1)
        self.assertEqual(audit['questions'][1]['id'],'item_2')

    def test_history_preserves_item_evidence_and_trace(self):
        from uuid import uuid4
        from dataquery_agent.query_history import build_record
        result,runtime,_=self.run_model([entry(1,'order_count: 1337'),entry(2)])
        answer={'status':'ok','task':'knowledge','result':dict(result,path='knowledge'),
            'answer_zh':result['answer_zh'],'trace':{'tools':[result['retrieval_trace']],'model_attempts':runtime.attempts}}
        saved=build_record({'conversation_id':uuid4(),'message_id':uuid4()},QUESTION,answer,'new',1)
        self.assertEqual(saved['knowledge']['evidence_items'],result['evidence_items'])
        self.assertEqual(saved['tool_traces'][0]['retrieval']['item_evidence']['supported_count'],1)

    def test_later_outer_recheck_removes_stale_nested_quotes(self):
        from dataquery_agent.rag.item_evidence import revalidate_result
        result,_,_=self.run_model([entry(1,'order_count: 1337'),entry(2,'payment_attempt_count: 1408')])
        revised=revalidate_result(result,[result['citations'][0]])
        self.assertEqual(revised['evidence_status'],'partial')
        self.assertEqual(revised['evidence_items'][1]['reason_code'],'source_invalidated')
        self.assertIsNone(revised['evidence_items'][1]['answer'])
        self.assertNotIn('1408',revised['answer_zh'])
        self.assertIn('1337',revised['answer_zh'])
        self.assertEqual(revised['retrieval_trace']['item_evidence']['supported_count'],1)

    def test_outer_document_recheck_retains_only_valid_child(self):
        from dataquery_agent.rag.routing import run_document_plan
        result,_,_=self.run_model([entry(1,'order_count: 1337'),entry(2,'payment_attempt_count: 1408')])
        agent=Mock();agent.service.metrics={};agent.knowledge.answer.return_value=result
        agent.knowledge.retriever.store.current_pdf_citations.return_value=[result['citations'][0]]
        def execute(plan,frame,trace,*,document,roles):
            event,payload=document({'question':QUESTION})
            event.update(task_id='p/2',attempt_id='a',kind='pdf')
            return {'pdf_result':payload,'pdf_summary':None,'plan_id':'p','answer_zh':'SQL summary',
                    'subtasks':[{'status':'ok'},event,{'status':'skipped'}]}
        agent.run_business_plan.side_effect=execute
        answer=run_document_plan(agent,{},AgentRuntime(Mock()),{}, {'tools':[]},date(2026,11,23),'owner')
        self.assertEqual(answer['knowledge_result']['evidence_status'],'partial')
        self.assertEqual(len(answer['knowledge_result']['citations']),1)
        self.assertIn('1337',answer['answer_zh'])
        self.assertNotIn('1408',answer['answer_zh'])
