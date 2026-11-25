"""Evidence-to-fact regressions, fixed responses only."""
import json
import unittest
from unittest.mock import Mock,patch
from datetime import date
from dataquery_agent.agent_runtime import AgentRuntime
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.item_evidence import split_pair

Q='根据已审核PDF，2025年1月web渠道的订单量和支付尝试次数分别是多少？'
ORDER='Period 2025-01; channel web; order_count: 1337.'
PAY='Period 2025-01; channel web; payment_attempt_count: 1408.'

def item(i,quote):return {'id':f'item_{i}','status':'supported','claims':[{'chunk_id':'same','quote':quote}]}

class BindingTests(unittest.TestCase):
 def run_fixed(self,quotes,*,source=None,current=True):
  evidence=[dict(chunk_id='same',evidence_text=source or '\n'.join([ORDER,PAY]),source_type='reviewed_pdf',title='Fixed',page_number=1,line_start=1,line_end=2,document_version='v1',source_uri='reviewed-pdf:fixed')]
  ret=Mock();ret.search.return_value=([{}],{});ret.store.current_pdf_citations.side_effect=lambda cs,**kw:cs if current else []
  model=Mock(return_value=json.dumps({'items':[item(i,q) for i,q in enumerate(quotes,1)]}))
  with patch('dataquery_agent.rag.answer.build_evidence',return_value=(evidence,[],[])):
   result=KnowledgeService(ret,MetricQueryService(None).metrics).answer(Q,AgentRuntime(model),today=date(2026,11,25),owner='owner',pdf_only=True)
  self.assertEqual(model.call_count,1);return result
 def test_swapped_quotes_fail_both(self):
  r=self.run_fixed([PAY,ORDER]);self.assertEqual(r['evidence_status'],'insufficient');self.assertFalse(r['citations'])
 def test_one_mismatch_preserves_correct_item(self):
  r=self.run_fixed([PAY,PAY]);self.assertEqual(r['evidence_status'],'partial');self.assertEqual([i['status'] for i in r['evidence_items']],['insufficient','supported'])
 def test_same_chunk_two_distinct_quotes_supported(self):
  r=self.run_fixed([ORDER,PAY]);self.assertEqual(r['evidence_status'],'supported');self.assertEqual(len(r['citations']),2)
 def test_wrong_value_month_channel_or_ambiguous_line(self):
  for bad in [ORDER.replace('1337','9999'),ORDER.replace('2025-01','2025-02'),ORDER.replace('web','mobile'),'order_count: 1337',ORDER+' '+PAY,'not '+ORDER]:
   with self.subTest(bad=bad):
    # Wrong value absent from source fails verbatim check; other lines exist but don't support this item.
    source='\n'.join([ORDER,PAY]+([] if '9999' in bad else [bad]))
    r=self.run_fixed([bad,PAY],source=source);self.assertEqual(r['evidence_status'],'partial');self.assertEqual(r['evidence_items'][1]['status'],'supported')
 def test_current_owner_version_check_precedes_binding(self):
  r=self.run_fixed([ORDER,PAY],current=False);self.assertEqual(r['evidence_status'],'insufficient');self.assertTrue(all(i['reason_code']=='source_invalidated' for i in r['evidence_items']))
 def test_explicit_child_conditions_preserve_original_path(self):
  for q in ['2025年1月web渠道订单量和mobile渠道支付尝试次数分别是多少？','2025年1月web渠道订单量和2025年2月web渠道支付尝试次数分别是多少？','2025年1月web渠道订单量和二月支付尝试次数分别是多少？']:
   with self.subTest(q=q):self.assertEqual(split_pair(q),[])
  self.assertEqual(len(split_pair(Q)),2)

 def test_partial_line_and_unknown_metric_fail_closed(self):
  r=self.run_fixed([ORDER,PAY],source='not '+ORDER+'\n'+PAY)
  self.assertEqual(r['evidence_status'],'partial')
  from dataquery_agent.rag.item_evidence import bind_support
  self.assertEqual(bind_support([dict(id='item_1',label='订单退款率',question='2025年1月web渠道的订单退款率是多少？',status='supported',citations=[dict(chunk_id='c',quote=ORDER)])],MetricQueryService(None).metrics,[dict(chunk_id='c',evidence_text=ORDER)])[0]['status'],'insufficient')
 def test_alias_reuses_catalog_and_child_channel_reaches_legacy_prompt(self):
  from dataquery_agent.rag.item_evidence import bind_support
  cs=[dict(chunk_id='c',quote=ORDER)]
  bound=bind_support([dict(id='item_1',label='下单笔数',question='2025年1月web渠道的下单笔数是多少？',status='supported',citations=cs)],MetricQueryService(None).metrics,[dict(chunk_id='c',evidence_text=ORDER)])
  self.assertEqual(bound[0]['status'],'supported')
  question='根据已审核PDF，2025年1月web渠道订单量和mobile渠道支付尝试次数分别是多少？'
  from dataquery_agent.unified_agent import UnifiedAgent
  ret=Mock();ret.search.return_value=([{}],{});ret.store.current_pdf_citations.side_effect=lambda cs,**_:cs
  def model(prompt):
   self.assertTrue(prompt.startswith('[knowledge-extractive-v1]'))
   payload=json.loads(prompt.split('\n',1)[1]);self.assertEqual(payload['question'],question);self.assertNotIn('subquestions',payload)
   return json.dumps({'status':'insufficient','claims':[]})
  evidence=[dict(chunk_id='c',title='Fixed',evidence_text=ORDER,source_type='reviewed_pdf')]
  with patch('dataquery_agent.rag.answer.build_evidence',return_value=(evidence,[],[])):
   result=UnifiedAgent(MetricQueryService(None),None,knowledge=KnowledgeService(ret,{})).answer(question,model,today=date(2026,11,25),knowledge_owner='owner')
  self.assertEqual(result['trace']['routing']['model_stages'],['knowledge']);self.assertNotIn('evidence_items',result['result'])
