"""Small real-backend paired acceptance. --live explicitly enables five query embeddings and one answer."""
import argparse
import asyncio
from contextlib import ExitStack
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_hex
import subprocess
from time import monotonic,sleep
import unicodedata
from unittest.mock import Mock
from uuid import uuid4

from dataquery_agent.agent_runtime import AgentRuntime
from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import owner_hash
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.answer import KnowledgeService,build_evidence,validate_answer
from dataquery_agent.rag.documents import KnowledgeError
from dataquery_agent.rag.embedding import GeminiEmbedding
from dataquery_agent.rag.milvus import Milvus,sync_visible
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.pdf_review import approve,build_document
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore,CHUNKS,VERSIONS,DOCS
from evaluate_integrated_e2e import stage_pdf,PDF
from evaluate_agent_e2e import safe_generate
from test_api import AUTH,TOKEN,client

TODAY=date(2026,11,20)
GOLDEN=ROOT/'tests/milvus_cases_v1.json'


class ExistingPdfVectors(GeminiEmbedding):
    """Reuse only vectors for this explicitly authorized synthetic original and exact input text."""
    def __init__(self,writer,digest):
        with writer.transaction() as pg:
            rows=pg.execute(f"""SELECT v.title,c.heading_path,c.chunk_text,c.embedding::text AS vector
                FROM {CHUNKS} c JOIN {VERSIONS} v USING(document_id,document_version)
                JOIN {DOCS} d USING(document_id)
                WHERE d.source_type='reviewed_pdf' AND v.content_hash=%s
                  AND v.pdf_review->>'file_sha256'=%s AND v.pdf_review->>'review_state'='reviewed'
                  AND c.embedding_model=%s AND c.embedding_version=%s""",
                (digest,digest,self.model,self.version)).fetchall()
        self.inputs={'\n'.join([r['title'],*r['heading_path'],r['chunk_text']]):r['vector'] for r in rows}
        self.used=[]

    def embed(self,texts,*,query=False):
        if query or any(text not in self.inputs for text in texts):
            raise KnowledgeError('authorized_existing_vector_missing')
        self.used.extend(sha256(text.encode()).hexdigest() for text in texts)
        return [self.inputs[text] for text in texts]


class QueryVectors(GeminiEmbedding):
    def __init__(self,questions,values):
        self.values=dict(zip(questions,values))

    def embed(self,texts,*,query=False):
        if not query or any(text not in self.values for text in texts):
            raise KnowledgeError('unbudgeted_query_embedding')
        return [self.values[text] for text in texts]


def stored_ids(milvus,ids):
    return milvus.request('entities/query',{'filter':'chunk_id in '+json.dumps(ids),
        'outputFields':['chunk_id'],'consistencyLevel':'Strong'})


async def run(args):
    golden=json.loads(GOLDEN.read_text())
    digest=sha256(PDF.read_bytes()).hexdigest()
    if digest!=golden['source_pdf_sha256']:raise ValueError('unauthorized_fixture_changed')
    writer,reader=KnowledgeStore.local(admin=True),KnowledgeStore.local()
    owner,foreign=token_hex(32),token_hex(32)
    milvus=Milvus.local();existing=ExistingPdfVectors(writer,digest)
    report={'mode':'real Milvus + pgvector, reused existing Gemini document vectors, fresh query vectors',
            'golden_sha256':sha256(GOLDEN.read_bytes()).hexdigest(),'top_k':golden['top_k'],
            'cases':[],'lifecycle':{},'generation_events':[],'errors':[]}
    query_embedding=MeasuredEmbedding.local()
    report['embedding_batches']=query_embedding.batches
    imported=[];manager=None
    def save():args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    try:
        with ExitStack() as cleanup:
            did,document=stage_pdf(writer,existing,owner,cleanup)
            report['document']={'document_id':did,'document_version':document.version,'source_sha256':digest,
                'pages':[part.location for part in document.passages], 'document_vector_inputs_reused':len(existing.used)}
            normalized=lambda text:' '.join(unicodedata.normalize('NFKC',text).split())
            questions=[normalized(c['question']) for c in golden['cases']]+[normalized(golden['live_question'])]
            embedding=QueryVectors(questions,query_embedding.embed(questions,query=True))
            report['query_vector_sha256']={q:sha256(v.encode()).hexdigest() for q,v in embedding.values.items()}
            first=sync_visible(reader,embedding,owner=owner,document_ids=[did],client=milvus)
            imported+=first['chunk_ids']
            count1=stored_ids(milvus,imported)
            second=sync_visible(reader,embedding,owner=owner,document_ids=[did],client=milvus)
            count2=stored_ids(milvus,imported)
            assert len(count1)==len(count2)==len(set(imported))==2
            report['imports']={'first':first,'second':second,'logical_records_first':len(count1),'logical_records_second':len(count2)}
            scope=dict(today=TODAY,owner=owner,reviewed_pdf=True,top_k=golden['top_k'])
            for case in golden['cases']:
                for mode in ('vector','hybrid'):
                    for backend in ('pgvector','milvus'):
                        began=monotonic()
                        rows,trace=Retriever(reader,embedding,mode=mode,backend=backend).search(case['question'],**scope)
                        assert trace['vector_backend']['actual']==backend
                        evidence=build_evidence(rows,{})[0]
                        relevant=[r['chunk_id'] for r in rows if case['page'] and r['source_location'].startswith(f"page:{case['page']};")
                                  and case['quote'] in r['chunk_text']]
                        rank=next((i for i,r in enumerate(rows,1) if r['chunk_id'] in relevant),None)
                        target=next((e for e in evidence if e['chunk_id'] in relevant),None)
                        # Fixed attribution proposal isolates retrieval from model randomness.
                        proposal={'status':'supported','claims':[{'chunk_id':target['chunk_id'],'quote':case['quote']}]} if target else {'status':'insufficient','claims':[]}
                        _,citations=validate_answer(json.dumps(proposal),evidence)
                        valid=reader.current_pdf_citations(citations,owner=owner)
                        report['cases'].append({'id':case['id'],'question':case['question'],'backend':backend,'mode':mode,
                            'hit':bool(rank) if case['page'] else None,'reciprocal_rank':1/rank if rank else (0 if case['page'] else None),
                            'citation_support':bool(valid) if case['page'] else None,'citation_method':'fixed attribution + verbatim/page validation',
                            'citations':valid,'trace':trace,'elapsed_ms':round((monotonic()-began)*1000)})
                        save()
            manager,_=local_manager();manager.agent.supervisor=False
            backend=manager.generate;outputs=[]
            def generate(prompt):
                event={'stage':prompt.split(']',1)[0].lstrip('[')};report['generation_events'].append(event)
                if len(report['generation_events'])>4:raise ValueError('model_budget')
                began=monotonic()
                try:
                    result=backend(prompt);event.update(status='ok',model=result.get('model'),usage=result.get('usage',{}));return result
                except Exception as error:
                    event.update(status='error',type=type(error).__name__);raise
                finally:event['elapsed_ms']=round((monotonic()-began)*1000)
            manager.generate=safe_generate(generate,outputs,document,golden['live_question'])
            manager.agent.knowledge.retriever=Retriever(reader,embedding,backend='milvus')
            app=create_app(token=TOKEN,manager_factory=lambda:(manager,owner))
            async with client(app) as http:
                cid=(await http.post('/api/conversations',headers=AUTH)).json()['conversation_id']
                live=(await http.post('/api/chat',headers=AUTH,json={'conversation_id':cid,'message_id':str(uuid4()),
                    'question':golden['live_question']})).json()
                history=(await http.get('/api/queries/'+live['query_id'],headers=AUTH)).json()
                report['live_request']={'response':live,'history_trace_equal':live['trace']==history['trace'],'model_outputs':outputs}
                report['live_request']['pdf_supported']=any(c.get('page_number')==2 and c['quote']=='Order count: 48' for c in live['citations'])
            for backend in ('pgvector','milvus'):
                for mode in ('vector','hybrid'):
                    rows,_=Retriever(reader,embedding,backend=backend,mode=mode).search(golden['cases'][0]['question'],**(scope|{'owner':foreign}))
                    assert not rows
            report['lifecycle']['foreign_owner_rejected']=True
            extraction=extract_pdf(PDF,owner=owner)
            receipt=approve(extraction,PDF,owner=owner,slug=document.source_uri.split(':',1)[1],title=document.title,
                basis='Re-reviewed original for version isolation test; text unchanged',corrections={},source_revision='milvus-v2')
            updated=writer.index(None,existing,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=PDF)
            assert updated['version']!=document.version
            empty,trace=Retriever(reader,embedding,backend='milvus',mode='vector').search(golden['cases'][0]['question'],**scope)
            assert not empty and trace['vector_backend']['actual']=='milvus' and trace['vector_backend']['fallback_reason'] is None
            report['lifecycle']['updated_version_empty_without_sync']={'old_vectors_still_present':len(stored_ids(milvus,imported)),
                'new_version':updated['version'],'trace':trace}
            updated_sync=sync_visible(reader,embedding,owner=owner,document_ids=[did],client=milvus)
            imported+=updated_sync['chunk_ids']
            for backend in ('pgvector','milvus'):
                rows,_=Retriever(reader,embedding,backend=backend).search(golden['cases'][0]['question'],**scope)
                assert rows and all(r['document_version']==updated['version'] for r in rows)
            report['lifecycle']['old_version_excluded']=True
            if args.fault:
                name='dataquery-milvus-v1-milvus-1'
                try:
                    subprocess.run(['docker','stop','--timeout','10',name],check=True,capture_output=True)
                    rows,trace=Retriever(reader,embedding,backend='milvus').search(golden['cases'][0]['question'],**scope)
                    assert rows and trace['vector_backend']['actual']=='pgvector' and trace['vector_backend']['fallback_reason']
                    report['lifecycle']['actual_stopped_fallback']=trace
                finally:subprocess.run(['docker','start',name],check=True,capture_output=True)
                attempts=[]
                for i in range(40):
                    try:
                        milvus.prepare()
                        rows,trace=Retriever(reader,embedding,backend='milvus').search(golden['cases'][0]['question'],**scope)
                        attempts.append(trace['vector_backend'])
                        if rows and trace['vector_backend']['actual']=='milvus':break
                    except Exception as error:attempts.append({'error_type':type(error).__name__})
                    sleep(1)
                else:raise ValueError('milvus_restart_failed')
                report['lifecycle']['restart_attempts']=attempts
                report['lifecycle']['restored_actual_backend']=trace['vector_backend']['actual']
            class WithdrawAfterCandidates:
                done=False
                def search(self,*args):
                    hits=milvus.search(*args)
                    if hits and not self.done:
                        writer.delete(did,owner=owner);self.done=True
                    return hits
            model=Mock(side_effect=AssertionError('withdrawn evidence must not reach model'))
            knowledge=KnowledgeService(Retriever(reader,embedding,backend='milvus',milvus=WithdrawAfterCandidates()),{})
            revoked=knowledge.answer(golden['cases'][0]['question'],AgentRuntime(model),today=TODAY,owner=owner,pdf_only=True)
            assert not revoked['citations'] and revoked['evidence_status']=='insufficient'
            model.assert_not_called()
            assert revoked['retrieval_trace']['vector_backend']['final_pg_rejected_ids']
            report['lifecycle']['withdrawn_during_search']={'trace':revoked['retrieval_trace'],
                'physical_old_vectors_still_present':len(stored_ids(milvus,imported)),'model_calls':0}
            for backend in ('pgvector','milvus'):
                for mode in ('vector','hybrid'):
                    rows,_=Retriever(reader,embedding,backend=backend,mode=mode).search(golden['cases'][0]['question'],**scope)
                    assert not rows
            report['lifecycle']['withdrawn_excluded']=True
    except Exception as error:
        report['errors'].append({'type':type(error).__name__,'code':str(error) if isinstance(error,KnowledgeError) else None})
    finally:
        if imported:
            try:milvus.request('entities/delete',{'filter':'chunk_id in '+json.dumps(imported)})
            except Exception as error:
                report['errors'].append({'type':type(error).__name__,'code':'milvus_test_cleanup_pending'})
        if manager:
            with manager.store.transaction() as pg:pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s',(owner_hash(owner),))
        report['summary']={}
        for mode in ('vector','hybrid'):
            for backend in ('pgvector','milvus'):
                cases=[r for r in report['cases'] if r['mode']==mode and r['backend']==backend and r['hit'] is not None]
                report['summary'][mode+'_'+backend]={'recall_hits':sum(c['hit'] for c in cases),'denominator':len(cases),
                    'reciprocal_rank_sum':sum(c['reciprocal_rank'] for c in cases),
                    'citation_support':sum(c['citation_support'] for c in cases),'na':sum(c['hit'] is None for c in report['cases'] if c['mode']==mode and c['backend']==backend)}
        save()
    print(json.dumps({'summary':report['summary'],'errors':report['errors'],'live_supported':report.get('live_request',{}).get('pdf_supported')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--fault',action='store_true')
    parser.add_argument('--out',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
