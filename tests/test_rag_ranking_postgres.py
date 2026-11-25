"""Opt-in real PostgreSQL authorization/version gates; fixed ranker, no paid calls."""
import json,os,unicodedata
from pathlib import Path
from secrets import token_hex
from datetime import date
from unittest.mock import patch
import unittest
from dotenv import dotenv_values
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag import pdf_review
from dataquery_agent.rag.store import KnowledgeStore
from dataquery_agent.rag.retrieval import Retriever
from evaluate_milvus import ExistingPdfVectors,QueryVectors
from evaluate_rag_ranking import ROOT,CONFIG

@unittest.skipUnless(os.getenv('RANKING_POSTGRES_TESTS')=='1','Explicit real PostgreSQL opt-in')
class RankingPostgresTests(unittest.TestCase):
 def test_rerank_midflight_version_change_and_revocation(self):
  writer=KnowledgeStore(dotenv_values(CONFIG/'.env.knowledge-admin')['KNOWLEDGE_DSN'],admin=True)
  reader=KnowledgeStore(dotenv_values(CONFIG/'.env.knowledge')['KNOWLEDGE_DSN'])
  gold=json.loads((ROOT/'tests/evaluation/rag_hard_evidence_v1.json').read_text());pdf=ROOT/gold['pdf_path']
  owner=token_hex(32);did=None;slug='rank-security-'+token_hex(6);existing=ExistingPdfVectors(writer,gold['pdf_sha256'])
  def publish(basis):
   with patch.object(pdf_review,'KEY_PATH',CONFIG/'.document-review-key'):
    receipt=pdf_review.approve(extract_pdf(pdf,owner=owner),pdf,owner=owner,slug=slug,title='SQL Shed aggregate evidence evaluation',basis=basis,corrections={str(p['page']):p['text'] for p in gold['pages']})
    return writer.index(None,existing,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=pdf)
  try:
   first=publish('Fixed-ranker security fixture review one');did=first['document_id']
   q='根据已审核PDF，2025年1月web渠道订单量是多少？'
   # Real stored document vector used only as a fixed security fixture, NOT quality ground truth.
   embedding=QueryVectors([unicodedata.normalize("NFKC",q)],[next(iter(existing.inputs.values()))])
   for keyword in ['postgres_fts','bm25']:
    ret=Retriever(reader,embedding,keyword_backend=keyword,backend='pgvector')
    self.assertFalse(ret.search(q,today=date(2026,11,25),owner=token_hex(32),reviewed_pdf=True)[0])
   from dataquery_agent.rag.answer import KnowledgeService
   from dataquery_agent.agent_runtime import AgentRuntime
   from dataquery_agent.metric_query_service import MetricQueryService
   def fixed_answer(prompt):
    payload=json.loads(prompt.split('\n',1)[1])
    quote='Period 2025-01; channel web; order_count: 1337.'
    match=next(r for r in payload['evidence'] if quote in r['text'])
    return json.dumps({'status':'supported','claims':[{'chunk_id':match['chunk_id'],'quote':quote}]})
   evidence=KnowledgeService(Retriever(reader,embedding,keyword_backend='bm25',backend='pgvector'),MetricQueryService(None).metrics).answer(q,AgentRuntime(fixed_answer),today=date(2026,11,25),owner=owner,pdf_only=True)
   self.assertEqual(evidence['evidence_status'],'supported');self.assertEqual(evidence['citations'][0]['page_number'],1)
   self.assertEqual(len(reader.current_pdf_citations(evidence['citations'],owner=owner)),1)
   class ChangeVersion:
    version='fixed-security';model=None
    def rank(self,query,rows):
     publish('Fixed-ranker security fixture review two')
     return [r['chunk_id'] for r in rows],{}
   rows,trace=Retriever(reader,embedding,keyword_backend='bm25',backend='pgvector',reranker=ChangeVersion()).search(q,today=date(2026,11,25),owner=owner,reviewed_pdf=True)
   self.assertFalse(rows);self.assertTrue(trace['rerank']['final_pg_rejected_ids'])
   current,_=Retriever(reader,embedding,keyword_backend='bm25',backend='pgvector').search(q,today=date(2026,11,25),owner=owner,reviewed_pdf=True)
   self.assertTrue(current);self.assertTrue(all(r['document_version']!=first['version'] for r in current))
   class Revoke:
    version='fixed-security';model=None
    def rank(self,query,rows):
     writer.delete(did,owner=owner)
     raise TimeoutError('fixed failure after revocation')
   rows,trace=Retriever(reader,embedding,keyword_backend='postgres_fts',backend='pgvector',reranker=Revoke()).search(q,today=date(2026,11,25),owner=owner,reviewed_pdf=True)
   self.assertFalse(rows);self.assertEqual(trace['rerank']['status'],'fallback');self.assertTrue(trace['rerank']['final_pg_rejected_ids'])
  finally:
   if did:writer.delete(did,owner=owner)
