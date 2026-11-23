"""Interchangeable candidate retrieval, with PG authorization before and after Milvus."""
from .milvus import Milvus,MilvusUnavailable,version_key


class PgVectorCandidates:
    name='pgvector'

    def search(self,pg,scored,params):
        rows=pg.execute(scored+"""SELECT * FROM scored
            ORDER BY (evidence_date_status='active') DESC,retrieval_score DESC,chunk_id
            LIMIT %(limit)s""",params).fetchall()
        return rows,{'requested':'pgvector','actual':'pgvector','fallback_reason':None,
                     'compatible_index':bool(rows)}


class MilvusCandidates:
    name='milvus'

    def __init__(self,client=None):
        self.client=client

    def search(self,pg,scored,params):
        manifest=pg.execute(scored+"""SELECT DISTINCT document_id,document_version,index_version,
                    evidence_date_status FROM scored""",params).fetchall()
        detail={'requested':'milvus','actual':'milvus','fallback_reason':None,
                'compatible_index':bool(manifest),'visible_versions':len(manifest)}
        try:
            client=self.client or Milvus.local()
            scope=pg.execute("SELECT current_setting('agent.owner_scope',true) AS owner").fetchone()['owner']
            hits=[]
            for active in (True,False):
                keys=[version_key(r['document_id'],r['document_version'],r['index_version']) for r in manifest
                      if (r['evidence_date_status']=='active')==active]
                hits+=client.search(params['embedding'],keys,['',scope],params['model'],params['version'],params['limit']-len(hits))
                if len(hits)>=params['limit']:break
            ids=[r['chunk_id'] for r in hits]
            detail['milvus_candidate_ids']=ids
            # Original text, dates, scores and provenance only come from PostgreSQL.
            rows=pg.execute(scored+"""SELECT * FROM scored WHERE chunk_id=ANY(%(candidate_ids)s)
                ORDER BY (evidence_date_status='active') DESC,retrieval_score DESC,chunk_id""",
                params|{'candidate_ids':ids}).fetchall() if ids else []
            detail['pg_rejected_ids']=sorted(set(ids)-{r['chunk_id'] for r in rows})
            return rows,detail
        except MilvusUnavailable as error:
            rows,_=PgVectorCandidates().search(pg,scored,params)
            return rows,dict(detail,actual='pgvector',fallback_reason=str(error))
