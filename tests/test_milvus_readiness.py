"""Replica readiness faults, offline and without external services."""
from unittest.mock import Mock, MagicMock, patch
import unittest

from dataquery_agent.rag.milvus import Milvus, MilvusUnavailable
from dataquery_agent.rag.milvus_readiness import index_ready, Readiness
import test_milvus_backend as backend_tests
from dataquery_agent.rag.vector_candidates import MilvusCandidates


class ReadinessTests(unittest.TestCase):
    def test_not_ready_falls_back_before_milvus(self):
        pg,client,params=backend_tests.MilvusBackendTests().setup_backend([{'chunk_id':'allowed'}])
        with patch('dataquery_agent.rag.milvus_readiness.index_ready',return_value=False):
            rows,trace=MilvusCandidates(client).search(pg,'WITH scored AS (...) ',params)
        self.assertEqual(rows,[{'chunk_id':'allowed'}])
        self.assertEqual(trace['fallback_reason'],'milvus_index_not_ready')
        self.assertEqual((trace['requested'],trace['actual']),('milvus','pgvector'))
        client.search.assert_not_called()

    def test_missing_migration_is_not_ready(self):
        pg=Mock();pg.execute.return_value.fetchone.return_value={'relation':None}
        self.assertFalse(index_ready(pg,'',{}))

    def test_verification_checks_all_batches_and_metadata(self):
        client=Milvus('http://127.0.0.1:19530','test-placeholder')
        records=[dict(chunk_id=str(i),version_key='v',owner_scope='owner',embedding_model='m',
                      embedding_version='e') for i in range(52)]
        with patch.object(client,'request',side_effect=[records[:50],records[50:]]) as call:
            client.verify(records)
        self.assertEqual(call.call_count,2)
        for bad in ([],[None],[{'chunk_id':[]}],[dict(records[-1],owner_scope='foreign')]):
            with patch.object(client,'request',side_effect=[records[:50],bad]):
                with self.assertRaisesRegex(MilvusUnavailable,'index_incomplete'):
                    client.verify(records)

    def test_changed_target_and_superseded_attempt_cannot_finish(self):
        row=dict(document_id='d',document_version='v',index_version='i',embedding_model='m',
                 embedding_version='e',chunk_id='c')
        writer=MagicMock(role='sqlshed_knowledge_admin')
        pg=writer.transaction.return_value.__enter__.return_value
        state=Readiness([row],writer)
        pg.execute.return_value.fetchone.return_value={'active_version':'new','active_index':'i','deleted_at':None}
        with self.assertRaisesRegex(MilvusUnavailable,'target_changed'):
            state.finish()
        with patch.object(state,'lock_and_check'):
            pg.execute.return_value.fetchone.return_value=None
            with self.assertRaisesRegex(MilvusUnavailable,'superseded'):
                state.finish()

    def test_partial_upsert_retry_reuses_primary_keys(self):
        client=Milvus('http://127.0.0.1:19530','test-placeholder')
        records=[{'chunk_id':str(i)} for i in range(52)]
        persisted={};calls=[]
        def request(operation,payload):
            calls.append(len(payload['data']))
            if len(calls)==2:raise MilvusUnavailable('injected_second_batch')
            persisted.update({r['chunk_id']:r for r in payload['data']})
        with patch.object(client,'request',side_effect=request):
            with self.assertRaises(MilvusUnavailable):client.upsert(records)
            self.assertEqual(len(persisted),50)
            client.upsert(records)
        self.assertEqual(calls,[50,2,50,2])
        self.assertEqual(len(persisted),52)
