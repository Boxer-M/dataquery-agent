"""Loopback-only Milvus REST adapter; no text, arbitrary expressions or script execution."""
from hashlib import sha256
import json
import re
from urllib.error import URLError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

from dotenv import dotenv_values

from ..metrics import ROOT
from .embedding import DIMENSIONS

COLLECTION='dataquery_chunks_v1'


class MilvusUnavailable(RuntimeError):
    pass


def version_key(document, version, index):
    return sha256(json.dumps([document,version,index],separators=(',',':')).encode()).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


class Milvus:
    def __init__(self,url,password):
        if url!='http://127.0.0.1:19530' or not password:
            raise MilvusUnavailable('milvus_not_configured')
        self.url,self.password=url,password

    @classmethod
    def local(cls):
        cfg=dotenv_values(ROOT/'.env.milvus')
        return cls(cfg.get('MILVUS_URL'),cfg.get('MILVUS_PASSWORD'))

    def request(self,operation,payload):
        if operation not in {'collections/has','collections/create','collections/describe','collections/load',
                             'entities/upsert','entities/search','entities/query','entities/delete','indexes/describe'}:
            raise ValueError('unapproved_milvus_operation')
        req=Request(self.url+'/v2/vectordb/'+operation,data=json.dumps(
            {'collectionName':COLLECTION,**payload}).encode(),headers={
                'Authorization':'Bearer root:'+self.password,'Content-Type':'application/json'})
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=3) as response:
                raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise MilvusUnavailable('milvus_response_limit')
            result=json.loads(raw)
            if result.get('code')!=0:
                raise MilvusUnavailable('milvus_api_error')
            return result.get('data',{})
        except (TimeoutError,URLError,OSError,ValueError) as error:
            if isinstance(error,MilvusUnavailable):raise
            timed=isinstance(error,TimeoutError) or isinstance(getattr(error,'reason',None),TimeoutError)
            raise MilvusUnavailable('milvus_timeout' if timed else 'milvus_connection_error') from None

    def prepare(self):
        if not self.request('collections/has',{}).get('has'):
            fields=[{'fieldName':name,'dataType':'VarChar','elementTypeParams':{'max_length':str(size)},
                     **({'isPrimary':True} if name=='chunk_id' else {})} for name,size in (
                     ('chunk_id',64),('document_id',64),('document_version',160),('index_version',64),
                     ('version_key',64),('owner_scope',64),('embedding_model',120),('embedding_version',160))]
            fields.append({'fieldName':'vector','dataType':'FloatVector','elementTypeParams':{'dim':str(DIMENSIONS)}})
            self.request('collections/create',{'schema':{'autoId':False,'enableDynamicField':False,'fields':fields},
                'indexParams':[{'fieldName':'vector','indexName':'vector_cosine','metricType':'COSINE','params':{'index_type':'FLAT'}}],
                'params':{'consistency_level':'Strong'}})
        description=self.request('collections/describe',{})
        fields={f['name']:f for f in description['fields']}
        if set(fields)!={'chunk_id','document_id','document_version','index_version','version_key',
                         'owner_scope','embedding_model','embedding_version','vector'}:
            raise MilvusUnavailable('milvus_schema_mismatch')
        dimensions={p['key']:p['value'] for p in fields['vector']['params']}
        if (dimensions.get('dim')!=str(DIMENSIONS) or fields['vector']['type']!='FloatVector'
                or not fields['chunk_id']['primaryKey'] or description['autoId']):
            raise MilvusUnavailable('milvus_schema_mismatch')
        indexes=self.request('indexes/describe',{'indexName':'vector_cosine'})
        if not indexes or indexes[0].get('metricType')!='COSINE' or indexes[0].get('indexType')!='FLAT':
            raise MilvusUnavailable('milvus_index_mismatch')
        self.request('collections/load',{})

    def upsert(self,records):
        for start in range(0,len(records),50):
            self.request('entities/upsert',{'data':records[start:start+50]})

    def search(self,embedding,keys,scopes,model,version,limit):
        if not keys:return []
        if len(keys)>2000:raise MilvusUnavailable('milvus_scope_limit')
        # All values are trusted PG metadata or hashes, serialized as string literals.
        condition=' and '.join(f'{name} in {json.dumps(values)}' for name,values in (
            ('version_key',keys),('owner_scope',scopes),('embedding_model',[model]),('embedding_version',[version])))
        rows=self.request('entities/search',{'data':[json.loads(embedding)],'annsField':'vector','filter':condition,
            'limit':limit,'outputFields':['chunk_id'],'consistencyLevel':'Strong',
            'searchParams':{'metricType':'COSINE','params':{}}})
        if not isinstance(rows,list) or any(
                not isinstance(row,dict) or not isinstance(row.get('chunk_id'),str) for row in rows):
            raise MilvusUnavailable('milvus_invalid_response')
        return rows


def sync_visible(store,embedding,*,owner=None,document_ids=None,synthetic=False,client=None):
    """Explicit replica import of current approved vectors already stored in PostgreSQL."""
    from .store import CHUNKS,DOCS,VERSIONS,READER
    if store.role!=READER:
        raise ValueError('milvus_import_requires_rls_reader')
    if document_ids is not None and (not isinstance(document_ids,list) or len(document_ids)>1000
            or any(not isinstance(did,str) or not re.fullmatch(r'[a-f0-9]{64}',did) for did in document_ids)):
        raise ValueError('invalid_import_document_scope')
    client=client or Milvus.local()
    with store.transaction(owner,synthetic=synthetic) as pg:
        rows=pg.execute(f"""SELECT c.chunk_id,c.document_id,c.document_version,c.index_version,
            d.owner_scope,c.embedding_model,c.embedding_version,c.embedding::text AS vector
            FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
            JOIN {VERSIONS} v USING(document_id,document_version)
            WHERE d.deleted_at IS NULL AND d.active_version=c.document_version AND d.active_index=c.index_version
              AND c.embedding_model=%s AND c.embedding_version=%s
              AND (%s::text[] IS NULL OR c.document_id=ANY(%s))
              AND (d.source_type<>'reviewed_pdf' OR (v.pdf_review->>'review_state'='reviewed'
                   AND v.pdf_review->>'file_sha256'=v.content_hash AND v.pdf_review->>'owner_scope'=d.owner_scope))
            ORDER BY c.chunk_id LIMIT 10001""",(embedding.model,embedding.version,document_ids,document_ids)).fetchall()
    if len(rows)>10000:raise ValueError('milvus_import_limit')
    records=[dict(row,version_key=version_key(row['document_id'],row['document_version'],row['index_version']),
                  vector=json.loads(row['vector'])) for row in rows]
    client.prepare()
    client.upsert(records)
    return {'upserted':len(records),'chunk_ids':[row['chunk_id'] for row in records]}
