"""Offline backend contract and fault tests; no external model, network or database."""
import json
import unittest
from unittest.mock import Mock,patch

from dataquery_agent.rag.milvus import Milvus,MilvusUnavailable,version_key,sync_visible
from dataquery_agent.rag.vector_candidates import MilvusCandidates
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.documents import KnowledgeError
from dataquery_agent.runtime_trace import tool_event


class MilvusBackendTests(unittest.TestCase):
    def setup_backend(self,rows,hits=None,error=None):
        pg=Mock()
        manifest=[{'document_id':'doc','document_version':'v1','index_version':'i1','evidence_date_status':'active'}]
        def execute(query,params=None):
            result=Mock()
            result.fetchall.return_value=manifest if 'DISTINCT' in query else rows
            result.fetchone.return_value={'owner':'owner-hash'}
            return result
        pg.execute.side_effect=execute
        milvus=Mock();milvus.search.return_value=hits or []
        if error:milvus.search.side_effect=error
        params={'embedding':'[1,0]','model':'test','version':'v1','limit':6}
        return pg,milvus,params

    def test_connection_timeout_falls_back_but_empty_does_not(self):
        row={'chunk_id':'allowed','evidence_date_status':'active','retrieval_score':1}
        for reason in ('milvus_timeout','milvus_connection_error'):
            pg,milvus,params=self.setup_backend([row],error=MilvusUnavailable(reason))
            rows,trace=MilvusCandidates(milvus).search(pg,'WITH scored AS (...) ',params)
            self.assertEqual(rows,[row]);self.assertEqual(trace['actual'],'pgvector')
            self.assertEqual(trace['fallback_reason'],reason)
        pg,milvus,params=self.setup_backend([])
        rows,trace=MilvusCandidates(milvus).search(pg,'WITH scored AS (...) ',params)
        self.assertEqual(rows,[]);self.assertEqual(trace['actual'],'milvus')
        self.assertIsNone(trace['fallback_reason'])
        self.assertFalse(any('SELECT * FROM scored' in call.args[0] for call in pg.execute.call_args_list))

    def test_server_scope_and_pg_rejection_of_untrusted_hits(self):
        pg,milvus,params=self.setup_backend([],hits=[{'chunk_id':'foreign'}])
        rows,trace=MilvusCandidates(milvus).search(pg,'WITH scored AS (...) ',params)
        self.assertEqual(rows,[]);self.assertEqual(trace['pg_rejected_ids'],['foreign'])
        args=milvus.search.call_args_list[0].args
        self.assertEqual(args[1],[version_key('doc','v1','i1')])
        self.assertEqual(args[2],['','owner-hash'])

    def test_rest_filter_is_metadata_only_and_metric_is_cosine(self):
        client=Milvus('http://127.0.0.1:19530','local-test-placeholder')
        with patch.object(client,'request',return_value=[]) as request:
            client.search('[1,0]',['version-hash'],['','owner'], 'model','version',2)
            operation,payload=request.call_args.args
            self.assertEqual(operation,'entities/search')
            self.assertEqual(payload['searchParams']['metricType'],'COSINE')
            self.assertIn('owner_scope in ["", "owner"]',payload['filter'])
            self.assertEqual(payload['consistencyLevel'],'Strong')
        with self.assertRaises(MilvusUnavailable):Milvus('https://external.invalid','secret')
        with self.assertRaises(ValueError):client.request('admin/arbitrary',{})

    def test_backend_selection_and_invalid_config(self):
        with patch.dict('os.environ',RAG_VECTOR_BACKEND='milvus'):
            self.assertEqual(Retriever(Mock(),Mock()).candidates.name,'milvus')
        self.assertEqual(Retriever(Mock(),Mock(),backend='pgvector').candidates.name,'pgvector')
        with self.assertRaises(KnowledgeError):Retriever(Mock(),Mock(),backend='unknown')
        with self.assertRaisesRegex(ValueError,'rls_reader'):
            sync_visible(Mock(role='sqlshed_knowledge_admin'),Mock())
        self.assertNotIn('vector_backend',tool_event({'channels':{},'mode':'hybrid'})['retrieval'])
        self.assertEqual(tool_event({'channels':{},'vector_backend':{'actual':'milvus'}})['retrieval']['vector_backend']['actual'],'milvus')
