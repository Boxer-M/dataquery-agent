"""Explicit paid two-case paired acceptance; one model call per scheduled trial."""
import argparse
import copy
from datetime import date,datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from secrets import token_hex
import subprocess
from time import monotonic,sleep
from types import ModuleType
from unittest.mock import patch
from dotenv import dotenv_values

from dataquery_agent.agent_runtime import AgentRuntime,ModelUnavailable
from dataquery_agent.agent_prompts import schema_for_prompt
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.intent_semantic_resolver import gemini_generator
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag import answer as candidate,item_evidence,pdf_review
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore,CHUNKS,DOCS
from evaluate_milvus import ExistingPdfVectors,QueryVectors


def assess(case,result,valid,model_ok,is_candidate):
    known=[i for i in case['items'] if i['expected_status']=='supported']
    outputs=result.get('evidence_items',[]) if is_candidate else []
    checked=[];wrong=0
    def matches(citation,expected):
        return (citation in valid and citation.get('page_number')==expected['required_page']
            and re.search(r'(?<![0-9.])'+re.escape(expected['value'])+r'(?![0-9]|\.[0-9])',citation['quote']) is not None)
    for expected in case['items']:
        matches_id=[i for i in outputs if i['id']==expected['id']]
        actual=matches_id[0] if len(matches_id)==1 else None
        citations=actual['citations'] if actual else ([] if is_candidate else result.get('citations',[]))
        if expected['expected_status']=='supported':
            retained=any(matches(c,expected) for c in citations)
            correct=retained and (not is_candidate or actual['status']=='supported')
        else:
            retained=None
            correct=(bool(actual) and actual['status']=='insufficient' and not citations
                and expected['label'] in result.get('answer_zh','') and '证据不足' in result.get('answer_zh','')) if is_candidate else (
                result.get('evidence_status') in {'partial','insufficient'} and all(any(matches(c,k) for k in known) for c in citations))
        if is_candidate:
            wrong+=sum(not (expected['expected_status']=='supported' and matches(c,expected)) for c in citations)
        checked.append({'id':expected['id'],'label':expected['label'],'expected':expected['expected_status'],
            'actual_status':actual['status'] if actual else None,'correct':bool(correct),
            'known_fact_retained':retained,'citations':citations,
            'association':'explicit_item_id' if is_candidate else 'evaluation_only_mapping_from_flat_quotes'})
    if not is_candidate:
        wrong=sum(not any(matches(c,k) for k in known) for c in result.get('citations',[]))
    return {'complete':bool(model_ok and result.get('evidence_status')==case['expected_status'] and all(i['correct'] for i in checked) and wrong==0),
        'known_retained':sum(bool(i['known_fact_retained']) for i in checked),'known_total':len(known),
        'wrong_citations':wrong,'model_ok':model_ok,'items':checked}


def run(args):
    config=dotenv_values(args.config_root/'.env');dataset=ROOT/'tests/evaluation/rag_hard_evidence_v1.json'
    golden=json.loads(dataset.read_text());labels=ROOT/'tests/evaluation/item_evidence_pair_v1.json'
    cases=json.loads(labels.read_text())['cases'];pdf=ROOT/golden['pdf_path']
    assert sha256(pdf.read_bytes()).hexdigest()==golden['pdf_sha256']
    base=ModuleType('dataquery_agent.rag._item_baseline');base.__package__='dataquery_agent.rag'
    exec(compile(subprocess.check_output(['git','show','e9d388f:src/dataquery_agent/rag/answer.py'],text=True),'<main-rag-answer>','exec'),base.__dict__)
    writer=KnowledgeStore(dotenv_values(args.config_root/'.env.knowledge-admin')['KNOWLEDGE_DSN'],admin=True)
    reader=KnowledgeStore(dotenv_values(args.config_root/'.env.knowledge')['KNOWLEDGE_DSN'])
    owner=token_hex(32);did=None;current={}
    report={'started_at':datetime.now(timezone.utc).isoformat(),'baseline':'e9d388f','source_dataset':'4a87fc2',
        'model':config['GEMINI_MODEL'],'golden_sha256':sha256(dataset.read_bytes()).hexdigest(),
        'item_labels_sha256':sha256(labels.read_bytes()).hexdigest(),'trials':[],'model_events':[],
        'embedding_events':[],'frozen':{},'errors':[],
        'scope':'Fresh real model answers with frozen pgvector hybrid evidence; main legacy vs item contract',
        'pacing_seconds':4,'runtime_policy':'One outbound model adapter call per trial; provider failure not retried or scored as refusal'}
    def save():args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    backend=gemini_generator(config,include_usage=True,schema_for_prompt=schema_for_prompt)
    def generate(prompt):
        payload=json.loads(prompt.split('\n',1)[1]);assert payload['question']==current['question']
        frozen=report['frozen'][payload['question']]
        assert [(e['chunk_id'],e['text']) for e in payload['evidence']]==[(e['chunk_id'],e['chunk_text']) for e in frozen['rows']]
        event={'trial_id':current['trial_id'],'version':current['version'],'case_id':current['case_id'],
               'prompt_sha256':sha256(prompt.encode()).hexdigest()}
        report['model_events'].append(event);start=monotonic()
        try:
            reply=backend(prompt);event.update(status='received',model=reply.get('model'),usage=reply.get('usage',{}),raw_text=reply['text'])
            try:event['raw_structured']=json.loads(reply['text'])
            except (ValueError,TypeError):event['raw_structured']=None
            return reply
        except Exception as error:
            event.update(status='error',http_status=getattr(error,'code',None),error_type=type(error).__name__)
            raise ModelUnavailable('evaluation_single_outbound_call_failure') from None
        finally:event['elapsed_ms']=round((monotonic()-start)*1000);save()
    class FrozenRetriever:
        store=reader
        def search(self,question,*,owner,**_):
            frozen=report['frozen'][question];rows=frozen['rows'];keys=[r['chunk_id'] for r in rows]
            with reader.transaction(owner) as pg:
                live=pg.execute(f'SELECT chunk_id,content_hash,chunk_text FROM {CHUNKS} WHERE chunk_id=ANY(%s)',(keys,)).fetchall()
            by={r['chunk_id']:r for r in live}
            assert set(by)==set(keys) and all(by[r['chunk_id']]['chunk_text']==r['chunk_text'] and by[r['chunk_id']]['content_hash']==r['content_hash'] for r in rows)
            current['pre_model_rls_check']=True
            return copy.deepcopy(rows),copy.deepcopy(frozen['trace'])
    try:
        existing=ExistingPdfVectors(writer,golden['pdf_sha256']);extraction=extract_pdf(pdf,owner=owner)
        with patch.object(pdf_review,'KEY_PATH',args.config_root/'.document-review-key'):
            receipt=pdf_review.approve(extraction,pdf,owner=owner,slug='item-evidence-'+token_hex(8),
                title='SQL Shed aggregate evidence evaluation',basis='Existing authorized six-page synthetic PDF; frozen independent ODS labels',
                corrections={str(p['page']):p['text'] for p in golden['pages']})
            document=pdf_review.build_document(receipt['review'])
            published=writer.index(None,existing,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=pdf)
        did=published['document_id'];report['document']={'document_id':did,'version':document.version,'sha256':document.content_hash,'new_document_embedding_calls':0}
        measured=MeasuredEmbedding(config['GEMINI_API_KEY']);queries=[c['question'] for c in cases]
        import unicodedata
        normalize=lambda q:' '.join(unicodedata.normalize('NFKC',q).split())
        values=measured.embed(list(map(normalize,queries)),query=True);report['embedding_events']=measured.batches
        report['query_vector_hashes']={q:sha256(v.encode()).hexdigest() for q,v in zip(queries,values)}
        frozen_vectors=QueryVectors(list(map(normalize,queries)),values)
        retriever=Retriever(reader,frozen_vectors,backend='pgvector',mode='hybrid')
        for case in cases:
            assert item_evidence.split_pair(case['question'])==[{k:i[k] for k in ('id','label','question')} for i in case['items']]
            rows,trace=retriever.search(case['question'],today=date.today(),owner=owner,reviewed_pdf=True,top_k=6)
            report['frozen'][case['question']]={'rows':rows,'trace':trace,'ordered_ids':[r['chunk_id'] for r in rows],
                'rows_sha256':sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()}
        for case in cases:
            for repeat in range(1,4):
                for version,module in (('main',base),('candidate',candidate)):
                    current={'trial_id':f"{case['case_id']}/{repeat}/{version}",'case_id':case['case_id'],'question':case['question'],
                        'repeat':repeat,'version':version,'validation_steps':[]}
                    report['trials'].append(current);runtime=AgentRuntime(generate)
                    original_items=item_evidence.validate;original_flat=module.validate_answer
                    def validate_items(raw,questions,evidence,validate_claim):
                        result=original_items(raw,questions,evidence,validate_claim)
                        current['validation_steps'].append({'contract':'items','validated_items_before_version_check':copy.deepcopy(result)})
                        return result
                    def validate_flat(raw,evidence):
                        result=original_flat(raw,evidence)
                        current['validation_steps'].append({'contract':'legacy','status':result[0],'citations':result[1]})
                        return result
                    with patch.object(item_evidence,'validate',validate_items),patch.object(module,'validate_answer',validate_flat):
                        result=module.KnowledgeService(FrozenRetriever(),MetricQueryService(None).metrics).answer(
                            case['question'],runtime,today=date.today(),owner=owner,pdf_only=True)
                    valid=reader.current_pdf_citations(result.get('citations',[]),owner=owner)
                    events=[e for e in report['model_events'] if e['trial_id']==current['trial_id']];assert len(events)==1
                    event=events[0];model_ok=event['status']=='received'
                    current.update(result=result,attempts=runtime.attempts,assessment=assess(case,result,valid,model_ok,version=='candidate'),
                        trial_status='completed' if model_ok else ('unfinished_http429' if event.get('http_status')==429 else 'unfinished_provider'))
                    save();print(current['trial_id'],result.get('evidence_status'),current['assessment']['complete'],flush=True)
                    if event.get('http_status')==400:raise RuntimeError('permanent_http400_stop')
                    sleep(4)
        summary={}
        for version in ('main','candidate'):
            trials=[t for t in report['trials'] if t['version']==version];events=[e for e in report['model_events'] if e['version']==version]
            summary[version]={'complete':sum(t['assessment']['complete'] for t in trials),'total':len(trials),
                'known_retained':sum(t['assessment']['known_retained'] for t in trials),'known_total':sum(t['assessment']['known_total'] for t in trials),
                'wrong_citations':sum(t['assessment']['wrong_citations'] for t in trials),'generation_calls':len(events),
                'http429':sum(e.get('http_status')==429 for e in events),'unfinished':sum(not t['assessment']['model_ok'] for t in trials),
                'tokens':{k:sum(e.get('usage',{}).get(k) or 0 for e in events) for k in ('prompt','output','total')}}
        report['summary']=summary
        report['valid_pairs']=sum(all(t['assessment']['model_ok'] for t in report['trials'] if t['case_id']==c['case_id'] and t['repeat']==r) for c in cases for r in range(1,4))
    except Exception as error:
        report['errors'].append({'type':type(error).__name__,'http_status':getattr(error,'code',None)});raise
    finally:
        if did:
            writer.delete(did,owner=owner)
            with writer.transaction() as pg:doc=pg.execute(f'SELECT deleted_at,active_version FROM {DOCS} WHERE document_id=%s',(did,)).fetchone()
            report['cleanup']={'withdrawn':doc['deleted_at'] is not None,'active_version':doc['active_version']}
        report['source_hashes']={p:sha256((ROOT/p).read_bytes()).hexdigest() for p in ('src/dataquery_agent/rag/answer.py','src/dataquery_agent/rag/item_evidence.py','src/dataquery_agent/agent_prompts.py')}
        save()
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--live',action='store_true',required=True)
    ap.add_argument('--config-root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    run(ap.parse_args())
