"""Opt-in real PostgreSQL/History smoke with fixed output; no Gemini calls."""
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
from secrets import token_hex
from unittest.mock import patch
from uuid import uuid4
import unittest

from dotenv import dotenv_values
from dataquery_agent.api_models import public_response
from dataquery_agent.conversation_manager import ConversationManager
from dataquery_agent.conversation_store import ConversationStore,ConversationError,owner_hash
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag import pdf_review
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from dataquery_agent.unified_agent import UnifiedAgent
from evaluate_milvus import ExistingPdfVectors,QueryVectors
from test_item_evidence import QUESTION


@unittest.skipUnless(os.getenv('ITEM_EVIDENCE_POSTGRES_TESTS')=='1','Explicit local PostgreSQL + signer opt-in')
class ItemEvidencePostgresTests(unittest.TestCase):
    def test_real_entry_history_owner_and_message_replay(self):
        root=Path(os.environ['DATAQUERY_TEST_CONFIG_ROOT'])
        writer=KnowledgeStore(dotenv_values(root/'.env.knowledge-admin')['KNOWLEDGE_DSN'],admin=True)
        reader=KnowledgeStore(dotenv_values(root/'.env.knowledge')['KNOWLEDGE_DSN'])
        golden=json.loads((ROOT/'tests/evaluation/rag_hard_evidence_v1.json').read_text())
        pdf=ROOT/golden['pdf_path'];self.assertEqual(sha256(pdf.read_bytes()).hexdigest(),golden['pdf_sha256'])
        existing=ExistingPdfVectors(writer,golden['pdf_sha256']);owner=token_hex(32);did=None;calls=[]
        store=ConversationStore(dotenv_values(root/'.env.agent-meta')['AGENT_META_DSN'])
        def fixed(prompt):
            self.assertTrue(prompt.startswith('[knowledge-items-v1]'))
            payload=json.loads(prompt.split('\n',1)[1]);calls.append(True)
            items=[]
            for item,value in zip(payload['subquestions'],('order_count: 1337','payment_attempt_count: 1408')):
                found=next(e for e in payload['evidence'] if value in e['text'])
                quote=next(line for line in found['text'].splitlines() if value in line)
                items.append({'id':item['id'],'status':'supported','claims':[{'chunk_id':found['chunk_id'],'quote':quote}]})
            return json.dumps({'items':items})
        try:
            extraction=extract_pdf(pdf,owner=owner)
            with patch.object(pdf_review,'KEY_PATH',root/'.document-review-key'):
                receipt=pdf_review.approve(extraction,pdf,owner=owner,slug='item-history-'+token_hex(8),
                    title='SQL Shed aggregate evidence evaluation',basis='Fixed-output real PG integration; existing reviewed test source',
                    corrections={str(p['page']):p['text'] for p in golden['pages']})
                did=writer.index(None,existing,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=pdf)['document_id']
            import unicodedata
            normalized=' '.join(unicodedata.normalize('NFKC',QUESTION).split())
            embedding=QueryVectors([normalized],[next(iter(existing.inputs.values()))])
            knowledge=KnowledgeService(Retriever(reader,embedding,backend='pgvector'),MetricQueryService(None).metrics)
            manager=ConversationManager(store,UnifiedAgent(MetricQueryService(None),None,knowledge=knowledge),fixed)
            cid=manager.create_conversation(owner)['conversation_id'];mid=uuid4()
            answer=manager.process_message(owner,cid,QUESTION,mid,today=date(2026,11,23))
            self.assertEqual(answer['result']['evidence_status'],'supported',answer)
            self.assertEqual([i['status'] for i in answer['result']['evidence_items']],['supported','supported'])
            saved=manager.get_query(owner,answer['query_id'])
            self.assertEqual(saved['knowledge']['evidence_items'],answer['result']['evidence_items'])
            self.assertEqual(public_response(answer).trace,public_response(saved,history=True).trace)
            with self.assertRaises(ConversationError):manager.get_query(token_hex(32),answer['query_id'])
            manager.process_message(owner,cid,QUESTION,mid,today=date(2026,11,23))
            self.assertEqual(len(calls),1)
        finally:
            if did:writer.delete(did,owner=owner)
            with store.transaction() as pg:pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s',(owner_hash(owner),))
