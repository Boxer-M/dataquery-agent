import math
import unittest
from unittest.mock import Mock
from dataquery_agent.rag.ranking import bm25,tokens,rerank

class RankingTests(unittest.TestCase):
 def test_bm25_formula_frequency_and_chinese(self):
  rows=[dict(chunk_id='1',title='',chunk_text='退款 退款'),dict(chunk_id='2',title='',chunk_text='订单')]
  result=bm25(rows,'退款',2)
  expected=math.log(1+(2-1+.5)/(1+.5))*2*2.2/(2+1.2*(.25+.75*2/1.5))
  self.assertAlmostEqual(result[0]['keyword_score'],expected)
  self.assertEqual([r['chunk_id'] for r in result],['1'])
  self.assertEqual(tokens('支付失败'),['支付','付失','失败'])
 def test_stable_tie_empty_and_independent_pool(self):
  rows=[dict(chunk_id=k,title='',chunk_text='exact') for k in ['b','a']]
  self.assertEqual([r['chunk_id'] for r in bm25(rows,'exact',2)],['a','b'])
  self.assertFalse(bm25(rows,'absent',2));self.assertFalse(bm25([],'exact',2))
 def test_rerank_top_n_then_tail_and_disabled(self):
  rows=[dict(chunk_id=str(i),title='',chunk_text='text') for i in range(3)]
  c=Mock();c.rank.return_value=(['1','0'],{})
  actual,trace=rerank('q',rows,c,2)
  self.assertEqual([r['chunk_id'] for r in actual],['1','0','2']);self.assertEqual(trace['status'],'ok')
  self.assertEqual(rerank('q',rows,None,2)[0],rows)
 def test_bad_rerank_timeout_failure_preserve_order(self):
  rows=[dict(chunk_id=i,title='',chunk_text='text') for i in ['a','b']]
  for output in [['a','a'],['a'],['a','other'],[None,'b']]:
   c=Mock();c.rank.return_value=(output,{})
   actual,trace=rerank('q',rows,c,2);self.assertEqual(actual,rows);self.assertEqual(trace['status'],'fallback')
  c=Mock();c.rank.side_effect=TimeoutError('secret must not leak')
  actual,trace=rerank('q',rows,c,2);self.assertEqual(actual,rows);self.assertNotIn('secret',str(trace))
 def test_no_external_call_on_oversize_or_empty(self):
  c=Mock();rows=[dict(chunk_id='a',title='',chunk_text='x'*24001)]
  actual,trace=rerank('q',rows,c,2);c.rank.assert_not_called();self.assertEqual(trace['model_calls'],0)
  rerank('q',[],c,2);c.rank.assert_not_called()
 def test_final_pg_revocation_after_rerank_success_or_failure(self):
  from datetime import date
  from unittest.mock import MagicMock
  from dataquery_agent.rag.retrieval import Retriever
  for failure in [False,True]:
   revoked=[False]
   row=dict(chunk_id='c',title='Title',chunk_text='exact',evidence_date_status='active',effective_date=None,expires_on=None,document_id='d',document_version='v',retrieval_score=1,exact_match=True,source_location='page:1;lines:1-1',heading_path=[],source_type='reviewed_pdf')
   store=MagicMock();pg=store.transaction.return_value.__enter__.return_value
   pg.execute.side_effect=lambda *args,**kwargs:Mock(fetchall=lambda:[] if revoked[0] else [dict(row)])
   embedding=Mock();embedding.embed.return_value=['[1]'];embedding.model='m';embedding.version='v'
   client=Mock()
   def rank(*args):
    revoked[0]=True
    if failure:raise TimeoutError()
    return ['c'],{}
   client.rank.side_effect=rank
   ret=Retriever(store,embedding,keyword_backend='bm25',backend='pgvector',reranker=client)
   ret.candidates=Mock();ret.candidates.search.return_value=([dict(row)],{'actual':'pgvector','compatible_index':True})
   rows,trace=ret.search('exact',today=date(2026,11,25),owner='owner',reviewed_pdf=True)
   self.assertFalse(rows);self.assertEqual(trace['rerank']['final_pg_rejected_ids'],['c'])
   self.assertTrue(all(c.args==('owner',) for c in store.transaction.call_args_list))
 def test_metric_denominators_and_ndcg(self):
  from evaluate_rag_ranking import quality,quantile
  self.assertEqual(quality([1,3],[3],2)['recall'],1)
  self.assertEqual(quality([1,3],[3],2)['mrr'],.5)
  self.assertAlmostEqual(quality([1,3],[3],2)['ndcg'],1/math.log2(3))
  self.assertIsNone(quality([1],[],2)['recall'])
  self.assertEqual(quality([1,1],[1],2)['ndcg'],1)
  self.assertEqual(quantile([1,3],.5),2)
 def test_configuration_defaults_and_invalid(self):
  from unittest.mock import patch
  from dataquery_agent.rag.retrieval import Retriever
  from dataquery_agent.rag.documents import KnowledgeError
  with patch.dict('os.environ',{'RAG_RERANK':'off','RAG_KEYWORD_BACKEND':'postgres_fts'}):
   default=Retriever(Mock(),Mock(),backend='pgvector')
   self.assertEqual(default.keyword_backend,'postgres_fts');self.assertIsNone(default.reranker)
   with self.assertRaises(KnowledgeError):Retriever(Mock(),Mock(),keyword_backend='unknown')
  with patch.dict('os.environ',{'RAG_RERANK':'unknown'}):
   with self.assertRaises(KnowledgeError):Retriever(Mock(),Mock())
