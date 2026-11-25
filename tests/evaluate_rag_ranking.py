"""Opt-in four-arm retrieval experiment; frozen vectors, labels, scoped reviewed PDF."""
import json,os,copy,math
from pathlib import Path
from hashlib import sha256
from secrets import token_hex
from datetime import date,datetime,timezone
from time import monotonic,sleep
from unittest.mock import patch
from dotenv import dotenv_values
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag import pdf_review
from dataquery_agent.rag.store import KnowledgeStore,DOCS
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.ranking import GeminiReranker
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.answer import KnowledgeService,build_evidence
from dataquery_agent.agent_runtime import AgentRuntime,ModelUnavailable
from dataquery_agent.agent_prompts import schema_for_prompt
from dataquery_agent.intent_semantic_resolver import gemini_generator
from dataquery_agent.metric_query_service import MetricQueryService
from evaluate_milvus import ExistingPdfVectors,QueryVectors

ROOT=Path(__file__).resolve().parents[1];CONFIG=Path('/Users/yxz/Documents/DataQuery Agent')
OUT=ROOT/'reports/warehouse/rag_ranking_v1.json'
TODAY=date(2026,11,25)

def quality(pages,relevant,k):
    if not relevant:return {'recall':None,'mrr':None,'ndcg':None,'reason':'no_relevant_evidence'}
    seen=set();gains=[]
    for p in pages[:k]:
        gains.append(int(p in relevant and p not in seen));seen.add(p)
    ideal=sum(1/math.log2(i+2) for i in range(min(k,len(relevant))))
    return dict(recall=len(set(pages[:k])&set(relevant))/len(relevant),mrr=next((1/(i+1) for i,g in enumerate(gains) if g),0),ndcg=sum(g/math.log2(i+2) for i,g in enumerate(gains))/ideal)

def quantile(values,q):
    if not values:return None
    a=sorted(values);x=(len(a)-1)*q;lo=int(x);return a[lo]+(a[min(lo+1,len(a)-1)]-a[lo])*(x-lo)

def run():
    labels_path=ROOT/'tests/evaluation/rag_ranking_v1.json';labels=json.loads(labels_path.read_text());gold=json.loads((ROOT/'tests/evaluation/rag_hard_evidence_v1.json').read_text());pdf=ROOT/gold['pdf_path'];assert sha256(pdf.read_bytes()).hexdigest()==labels['source_pdf_sha256']
    cfg=dotenv_values(CONFIG/'.env');writer=KnowledgeStore(dotenv_values(CONFIG/'.env.knowledge-admin')['KNOWLEDGE_DSN'],admin=True);reader=KnowledgeStore(dotenv_values(CONFIG/'.env.knowledge')['KNOWLEDGE_DSN'])
    owner=token_hex(32);did=None;blocked=False
    private=Path('/private/tmp')/('rag-ranking-'+token_hex(6));private.mkdir(mode=0o700)
    report=dict(started_at=datetime.now(timezone.utc).isoformat(),source_commit=os.popen('git rev-parse HEAD').read().strip(),labels=labels,labels_hash=sha256(labels_path.read_bytes()).hexdigest(),model=cfg['GEMINI_MODEL'],runs=[],model_events=[],answers=[],errors=[],protocol='A/B use PostgreSQL FTS or local BM25; C/D add independent Gemini listwise reranking. One shared query-vector batch. No answer generation per group. K2 N6. Groups alternate order per question. No tuning after labels frozen.',budget={'rerank_calls':20,'answer_calls':2,'embedding_batches':1})
    def save():OUT.parent.mkdir(exist_ok=True,parents=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    live=GeminiReranker(cfg['GEMINI_API_KEY'],cfg['GEMINI_MODEL'],timeout_ms=10000)
    class Measured:
        version=live.version;model=live.model
        def rank(self,q,rows):
            nonlocal blocked
            assert all(r['document_id']==did for r in rows)
            e=dict(kind='rerank',question=q,input_ids=[r['chunk_id'] for r in rows]);report['model_events'].append(e);t=monotonic()
            try:
                ids,meta=live.rank(q,rows);e.update(status='ok',ordered_ids=ids,**meta);return ids,meta
            except Exception as err:
                e.update(status='error',http_status=getattr(err,'code',None),type=type(err).__name__)
                if e['http_status'] in [400,403]:blocked=True
                raise
            finally:e['elapsed_ms']=round((monotonic()-t)*1000);save()
    measured=Measured();cached={}
    try:
        old=ExistingPdfVectors(writer,gold['pdf_sha256']);ex=extract_pdf(pdf,owner=owner)
        with patch.object(pdf_review,'KEY_PATH',CONFIG/'.document-review-key'):
            receipt=pdf_review.approve(ex,pdf,owner=owner,slug='ranking-'+token_hex(8),title='SQL Shed aggregate evidence evaluation',basis='Previously authorized synthetic PDF, fixed evidence labels for retrieval four-arm experiment',corrections={str(p['page']):p['text'] for p in gold['pages']})
            published=writer.index(None,old,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=pdf)
        did=published['document_id'];report['document']=published;report['document_vectors_reused']=len(old.used)
        queries=[c['question'] for c in labels['cases']]
        import unicodedata
        normalize=lambda q:' '.join(unicodedata.normalize('NFKC',q).split())
        emb=MeasuredEmbedding(cfg['GEMINI_API_KEY']);t=monotonic()
        try:values=emb.embed([normalize(q) for q in queries],query=True)
        except Exception as error:
            report['embedding_failure']=dict(http_status=getattr(error,'code',None),type=type(error).__name__,elapsed_ms=round((monotonic()-t)*1000));raise
        report['embedding_events']=emb.batches;report['embedding_model']=emb.model;report['embedding_version']=emb.version;report['vector_hashes']=[sha256(v.encode()).hexdigest() for v in values]
        manifest=private/'private.json';manifest.write_text(json.dumps(dict(owner=owner,vectors=values,queries=queries,receipt=receipt)));manifest.chmod(0o600)
        vectors=QueryVectors([normalize(q) for q in queries],values)
        for i,case in enumerate(labels['cases']):
            for arm in (['A','B','C','D'] if i%2==0 else ['D','C','B','A']):
                if blocked and arm in 'CD':
                    report['runs'].append(dict(case_id=case['case_id'],arm=arm,status='not_executed_permanent_model_error'));continue
                started=monotonic()
                rows,trace=Retriever(reader,vectors,backend='pgvector',keyword_backend='bm25' if arm in 'BD' else 'postgres_fts',reranker=measured if arm in 'CD' else None,rerank_top_n=labels['top_n']).search(case['question'],today=TODAY,owner=owner,reviewed_pdf=True,top_k=labels['top_k'])
                pages=[int(r['source_location'].split(';')[0].split(':')[1]) for r in rows]
                run=dict(case_id=case['case_id'],arm=arm,status='ok',pages=pages,trace=trace,latency_ms=(monotonic()-started)*1000,quality=quality(pages,case['relevant_pages'],labels['top_k']),retrieved_quotes=[r['chunk_text'] for r in rows])
                report['runs'].append(run);cached[(case['case_id'],arm)]=(rows,trace);save();print(case['case_id'],arm,pages,run['quality'],trace['rerank']['status'],flush=True)
                if arm in 'CD':sleep(2)
        # Isolated permissions/version checks, using real PG and an injected fixed ranker.
        q=queries[0];ret=Retriever(reader,vectors,backend='pgvector',keyword_backend='bm25')
        report['security']={'foreign_owner_no_rows':not ret.search(q,today=TODAY,owner=token_hex(32),reviewed_pdf=True)[0]}
        # At most two independent answer calls, only if provider not permanently unavailable.
        class ReplayRetrieval:
            store=reader
            def __init__(self,key):self.key=key
            def search(self,*args,**kwargs):return copy.deepcopy(cached[self.key])
        backend=gemini_generator(cfg,include_usage=True,schema_for_prompt=schema_for_prompt)
        for cid in ['alias-jan-web','no-march']:
            if blocked:
                report['answers'].append(dict(case_id=cid,status='not_executed_permanent_model_error'));continue
            case=next(c for c in labels['cases'] if c['case_id']==cid)
            arm='D' if (cid,'D') in cached else 'A'
            def generate(prompt):
                nonlocal blocked
                e={'kind':'answer','case_id':cid};report['model_events'].append(e);start=monotonic()
                try:
                    raw=backend(prompt);e.update(status='ok',raw=raw['text'],usage=raw.get('usage'));return raw
                except Exception as error:
                    e.update(status='error',http_status=getattr(error,'code',None),type=type(error).__name__)
                    if e['http_status'] in [400,403]:blocked=True
                    raise ModelUnavailable('evaluation_provider_error') from None
                finally:e['elapsed_ms']=round((monotonic()-start)*1000);save()
            result=KnowledgeService(ReplayRetrieval((cid,arm)),MetricQueryService(None).metrics).answer(case['question'],AgentRuntime(generate),today=TODAY,owner=owner,pdf_only=True)
            current=reader.current_pdf_citations(result['citations'],owner=owner)
            response_ok=report['model_events'][-1]['status']=='ok'
            passed=response_ok and (result['evidence_status']=='supported' and any(c['quote']==case['expected_quote'] for c in current) if case['relevant_pages'] else result['evidence_status']=='insufficient' and not result['citations'])
            report['answers'].append(dict(case_id=cid,arm=arm,result=result,correct=bool(passed),response_received=response_ok));save()
        report['summary']={}
        for arm in 'ABCD':
            runs=[r for r in report['runs'] if r['arm']==arm and r['status']=='ok'];valid=[r for r in runs if r['quality']['recall'] is not None]
            report['summary'][arm]=dict(requests=len(runs),answerable_denominator=len(valid),no_answer_na=len(runs)-len(valid),metrics={k:dict(sum=sum(r['quality'][k] for r in valid),denominator=len(valid),mean=sum(r['quality'][k] for r in valid)/len(valid) if valid else None) for k in ['recall','mrr','ndcg']},p50_ms=quantile([r['latency_ms'] for r in runs],.5),p95_ms=quantile([r['latency_ms'] for r in runs],.95),rerank_ok=sum(r['trace']['rerank']['status']=='ok' for r in runs),rerank_fallback=sum(r['trace']['rerank']['status']=='fallback' for r in runs))
    except Exception as error:
        report['errors'].append(dict(type=type(error).__name__,http_status=getattr(error,'code',None)));raise
    finally:
        if did:
            writer.delete(did,owner=owner)
            with writer.transaction() as pg:d=pg.execute(f'SELECT deleted_at,active_version FROM {DOCS} WHERE document_id=%s',(did,)).fetchone()
            report['cleanup']=dict(withdrawn=d['deleted_at'] is not None,active_version=d['active_version'])
        save()
    print(json.dumps(report.get('summary'),ensure_ascii=False),flush=True)

if __name__=='__main__':
    import sys
    if sys.argv[1:]!=['--live']:raise SystemExit('Explicit --live required (max20 rerank +2 answer calls).')
    run()
