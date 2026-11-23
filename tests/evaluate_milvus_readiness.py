"""Explicit local PostgreSQL/Milvus fault acceptance; zero external model calls."""
import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory

from pypdf import PdfReader, PdfWriter

from dataquery_agent.conversation_store import owner_hash
from dataquery_agent.metadata_service import fingerprint
from dataquery_agent.rag.answer import build_evidence, validate_answer
from dataquery_agent.rag.documents import KnowledgeError
from dataquery_agent.rag.milvus import Milvus, MilvusUnavailable, sync_visible
from dataquery_agent.rag.milvus_readiness import TABLE
from dataquery_agent.rag.pdf_review import approve, build_document
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore, CHUNKS
from dataquery_agent.document_ingestion import extract_pdf
from evaluate_milvus import ExistingPdfVectors, PDF


class ProbeVectors:
    """Real stored document vector reused as ANN probe, NOT a fresh query embedding."""
    def __init__(self, existing):
        self.model,self.version=existing.model,existing.version
        self.value=next(v for text,v in existing.inputs.items() if 'Order count: 48' in text)

    def embed(self,texts,*,query=False):
        if not query:raise AssertionError('probe_query_only')
        return [self.value for _ in texts]


class FaultClient(Milvus):
    def __init__(self,base,callback):
        super().__init__(base.url,base.password)
        self.batch_sizes=[]
        self.callback=callback

    def request(self,operation,payload):
        if operation=='entities/upsert':
            self.batch_sizes.append(len(payload['data']))
            if len(self.batch_sizes)==2:
                self.callback()
        return super().request(operation,payload)


def run(out):
    writer,reader=KnowledgeStore.local(admin=True),KnowledgeStore.local()
    milvus=Milvus.local()
    original_hash=sha256(PDF.read_bytes()).hexdigest()
    assert original_hash=='8716be31f972a13bf13b2f2d1daa84913859c6d327e4d501aa8a7df490400c28'
    existing=ExistingPdfVectors(writer,original_hash)
    probe=ProbeVectors(existing)
    owner=token_hex(32)
    report={'started_at':datetime.now(timezone.utc).isoformat(), 'external_embedding_calls':0,
            'generation_calls':0,'query_vector_mode':'stored real document embedding as ANN probe',
            'fixture':'local derivative of previously authorized synthetic PDF; exact text/vector reuse',
            'source_pdf_sha256':original_hash,'events':{},'cleanup':[]}
    docs=[];all_ids=set()
    def state():
        with writer.transaction() as pg:
            return pg.execute(f'SELECT document_id,document_version,attempt_id,state,cardinality(expected_ids) AS expected '
                              f'FROM {TABLE} WHERE document_id=ANY(%s) ORDER BY document_id',
                              ([d['id'] for d in docs],)).fetchall()
    def retrieve():
        rows,trace=Retriever(reader,probe,backend='milvus',mode='hybrid').search(
            'Order count: 48',today=date.today(),owner=owner,reviewed_pdf=True,top_k=2)
        evidence,_,_=build_evidence(rows,{})
        matching=next(e for e in evidence if 'Order count: 48' in e['evidence_text'])
        status,citations=validate_answer(json.dumps({'status':'supported','claims':[
            {'chunk_id':matching['chunk_id'],'quote':'Order count: 48'}]}),evidence)
        assert status=='supported' and len(citations)==1
        assert citations[0]['page_number']==12
        assert reader.current_pdf_citations(citations,owner=owner)==citations
        return {'trace':trace,'citation':citations[0]}
    try:
        with TemporaryDirectory(prefix='milvus-readiness-') as temp:
            source=PdfReader(PDF)
            # Forty native pages followed, in a separate PDF, by eleven native + one OCR page.
            # This stays within the production 40-page limit and places the unique answer in batch two.
            for n,pages in enumerate(([0]*40,[0]*11+[1])):
                path=Path(temp)/f'fixture-{n}.pdf'
                pdf=PdfWriter()
                for page in pages:pdf.add_page(source.pages[page])
                with path.open('wb') as stream:pdf.write(stream)
                extraction=extract_pdf(path,owner=owner)
                while True:
                    slug='readiness-'+token_hex(8)
                    did=fingerprint([owner_hash(owner),'reviewed_pdf','reviewed-pdf:'+slug])
                    if not docs or did>docs[0]['id']:break
                kwargs=dict(owner=owner,slug=slug,title='Synthetic PDF verification',
                            basis='Local synthetic pages inspected; unchanged original text, reused exact stored vectors',corrections={})
                receipt=approve(extraction,path,**kwargs)
                indexed=writer.index(None,existing,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=path)
                doc=build_document(receipt['review'])
                assert indexed['document_id']==did and len(doc.passages)==len(pages)
                docs.append(dict(id=did,path=path,extraction=extraction,kwargs=kwargs,version=doc.version))
            ids=[d['id'] for d in docs]
            with reader.transaction(owner) as pg:
                rows=pg.execute(f'SELECT chunk_id,document_id,chunk_index FROM {CHUNKS} '
                                'WHERE document_id=ANY(%s) ORDER BY document_id,chunk_index',(ids,)).fetchall()
            assert len(rows)==52
            all_ids.update(r['chunk_id'] for r in rows)
            missing_id=rows[-1]['chunk_id']
            report['expected_chunks']=52
            report['documents']=[{'document_id':d['id'],'version':d['version'],
                'sha256':sha256(d['path'].read_bytes()).hexdigest()} for d in docs]
            def fail_second():
                first=milvus.request('entities/query',{'filter':'chunk_id in '+json.dumps([r['chunk_id'] for r in rows]),
                    'outputFields':['chunk_id'],'consistencyLevel':'Strong','limit':100})
                assert len(first)==50 and missing_id not in {r['chunk_id'] for r in first}
                report['first_batch_confirmed']=len(first)
                report['during_sync']=retrieve()
                assert report['during_sync']['trace']['vector_backend']['fallback_reason']=='milvus_index_not_ready'
                raise MilvusUnavailable('injected_second_batch_failure')
            faulty=FaultClient(milvus,fail_second)
            try:sync_visible(reader,existing,owner=owner,document_ids=ids,client=faulty)
            except MilvusUnavailable as error:
                assert str(error)=='injected_second_batch_failure'
            else:raise AssertionError('fault_not_triggered')
            report['failed_batch_sizes']=faulty.batch_sizes
            report['after_failure']=retrieve()
            assert report['after_failure']['citation']['chunk_id']==missing_id
            assert report['after_failure']['trace']['vector_backend']['actual']=='pgvector'
            report['failed_states']=state()
            retry=FaultClient(milvus,lambda:None)
            report['retry']=sync_visible(reader,existing,owner=owner,document_ids=ids,client=retry)
            stored=milvus.request('entities/query',{'filter':'chunk_id in '+json.dumps(sorted(all_ids)),
                'outputFields':['chunk_id'],'consistencyLevel':'Strong','limit':100})
            assert len(stored)==len({r['chunk_id'] for r in stored})==52
            report['retry_batch_sizes']=retry.batch_sizes
            report['verified_unique_chunks']=len(stored)
            report['after_retry']=retrieve()
            assert report['after_retry']['trace']['vector_backend']['actual']=='milvus'
            assert report['after_retry']['citation']['chunk_id']==missing_id
            report['ready_states']=state()
            # Publish a new reviewed version while an old sync is still writing.
            def update_version():
                d=docs[-1]
                receipt=approve(d['extraction'],d['path'],**d['kwargs'],source_revision='readiness-v2')
                writer.index(None,existing,owner=owner,external_approved=True,pdf_receipt=receipt,pdf=d['path'])
                report['updated_version']=build_document(receipt['review']).version
            changed=FaultClient(milvus,update_version)
            try:sync_visible(reader,existing,owner=owner,document_ids=ids,client=changed)
            except MilvusUnavailable as error:assert str(error)=='milvus_sync_target_changed'
            else:raise AssertionError('stale_sync_marked_ready')
            report['changed_states']=state()
            changed_query=retrieve()
            assert changed_query['trace']['vector_backend']['fallback_reason']=='milvus_index_not_ready'
            assert changed_query['citation']['document_version']==report['updated_version']
            report['after_version_change']=changed_query
            recovery=sync_visible(reader,existing,owner=owner,document_ids=ids,client=milvus)
            all_ids.update(recovery['chunk_ids'])
            assert retrieve()['trace']['vector_backend']['actual']=='milvus'
            # Withdraw during the following write; old job may not mark either target ready.
            withdrawn=FaultClient(milvus,lambda:writer.delete(docs[-1]['id'],owner=owner))
            try:sync_visible(reader,existing,owner=owner,document_ids=ids,client=withdrawn)
            except MilvusUnavailable as error:assert str(error)=='milvus_sync_target_changed'
            else:raise AssertionError('withdrawn_sync_marked_ready')
            visible,trace=Retriever(reader,probe,backend='milvus').search(
                'Order count: 48',today=date.today(),owner=owner,reviewed_pdf=True,top_k=2)
            assert all(r['document_id']!=docs[-1]['id'] for r in visible)
            report['withdrawn_states']=state()
            report['after_withdrawal']={'trace':trace,'withdrawn_document_excluded':True}
    finally:
        for d in docs:
            writer.delete(d['id'],owner=owner)
        if all_ids:
            milvus.request('entities/delete',{'filter':'chunk_id in '+json.dumps(sorted(all_ids))})
        report['cleanup']=['all fixture documents withdrawn','all fixture Milvus vectors deleted']
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    print('Real PostgreSQL + Milvus acceptance passed: 52 chunks; batches 50 + 2; no external models')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-services',action='store_true',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    run(args.out)
