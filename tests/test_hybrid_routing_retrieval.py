from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
from secrets import token_hex
import unittest
from unittest.mock import Mock, patch

from dataquery_agent.conversation_context import edit_for_message, ContextError
from dataquery_agent.rag.retrieval import Retriever, keyword_terms, rrf
from dataquery_agent.runtime_trace import record_routing
import test_unified_agent as unified_fixture
from test_unified_agent import CASES


class HybridTests(unittest.TestCase):
    def test_rrf_and_independent_keyword_candidate(self):
        rows = lambda ids: [{'chunk_id': i, 'evidence_date_status': 'active'} for i in ids]
        ranked = rrf(rows(['vector', 'both']), rows(['keyword', 'both']))
        self.assertEqual(ranked[0]['chunk_id'], 'both')
        self.assertAlmostEqual(ranked[0]['rrf_score'], 2/62)
        self.assertEqual({r['chunk_id'] for r in ranked}, {'vector', 'keyword', 'both'})
        self.assertEqual(keyword_terms('退款审核 SKU_ZXQ 48'), ['sku_zxq', '48', '退款', '款审', '审核'])

    def test_clear_metric_and_ambiguous_return_do_not_call_model(self):
        fixture = unified_fixture.UnifiedAgentTests(); fixture.setUp()
        case = next(c for c in CASES.values() if c.get('metric') == 'order_count' and c['status'] == 'ok')
        fixture.service.query.side_effect = lambda request: fixture.fake_query(case, request)
        model = Mock(side_effect=AssertionError('unexpected model'))
        result = fixture.agent.answer('2025年1月订单量是多少？', model, today=date(2026, 11, 19))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['trace']['routing']['rule'], 'exact_metric_explicit_window')
        result = fixture.agent.answer('退款最多', model)
        self.assertEqual(result['status'], 'clarify')
        self.assertEqual({c['metric_id'] for c in result['choices']}, {'return_record_count', 'return_record_amount'})
        self.assertEqual(fixture.agent.answer('退款最多的净收入', model)['code'], 'missing_financial_definition')
        model.assert_not_called()

    def test_followup_inherits_only_confirmed_and_preserves_explicit_edit(self):
        prior = {'kind': 'standard', 'request': {'metric_id': 'order_count', 'start_date': '2025-01-01',
                 'end_date': '2025-02-01', 'filters': {'channel': 'web'}, 'group_by': ['channel']}}
        original = deepcopy(prior)
        operation, request = edit_for_message('那 2 月呢', prior, {}, '2026-01-01')
        self.assertEqual(request['start_date'], '2025-02-01')
        self.assertEqual(request['filters'], {'channel': 'web'})
        operation, request = edit_for_message('那 2 月，只看mobile渠道', prior, {}, '2026-01-01')
        self.assertEqual(request['filters'], {'channel': 'mobile'})
        self.assertEqual(request['group_by'], ['channel'])
        self.assertEqual(prior, original)
        answer = {'status': 'ok', 'task': 'single', 'trace': {'model_attempts': []}}
        record_routing(answer, prior, {'request': request}, operation)
        self.assertEqual(answer['trace']['routing']['inherited']['metric_id'], 'order_count')
        self.assertEqual(answer['trace']['routing']['modified']['filters']['after']['channel'], 'mobile')
        with self.assertRaises(ContextError):
            edit_for_message('那2月和3月呢', prior, {}, '2026-01-01')


@unittest.skipUnless(os.getenv('HYBRID_POSTGRES_TESTS') == '1', 'pgvector + local OCR opt-in')
class HybridPostgresTests(unittest.TestCase):
    def test_keyword_only_pdf_page_owner_withdrawal_and_chinese_fts(self):
        from dataquery_agent.document_ingestion import extract_pdf
        from dataquery_agent.rag.pdf_review import approve
        from dataquery_agent.rag.store import KnowledgeStore
        from dataquery_agent.rag.embedding import vector, DIMENSIONS
        from dataquery_agent.rag.answer import KnowledgeService
        from dataquery_agent.agent_runtime import AgentRuntime
        from evaluate_business_knowledge import FixedEmbedding
        from dataquery_agent.metrics import ROOT
        class ChannelProbe(FixedEmbedding):
            model = 'fixed-channel-independence-proof'
            version = 'fixed-channel-independence-proof-v1'
            def embed(self, texts, *, query=False):
                return [vector(([1, 0] if query or '48' not in text else [0, 1]) + [0] * (DIMENSIONS - 2)) for text in texts]
        owner, foreign = token_hex(32), token_hex(32)
        writer, reader, embedding = KnowledgeStore.local(admin=True), KnowledgeStore.local(), ChannelProbe()
        path = ROOT / 'output/pdf/document-extraction-sample.pdf'
        extraction = extract_pdf(path, owner=owner)
        documents = []
        try:
            for i in range(7):  # Seven page-1 distractors exceed the vector pool LIMIT (3*K=6).
                receipt = approve(extraction, path, owner=owner, slug='hybrid-proof-'+token_hex(8),
                                  title='验收原件', basis='Compared with synthetic PDF original, no text corrections', corrections={})
                documents.append(writer.index(None, embedding, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=path))
            search = Retriever(reader, embedding)
            rows, trace = search.search('48', today=date(2026, 11, 19), owner=owner, reviewed_pdf=True, top_k=2)
            self.assertEqual(len(trace['channels']['vector_pool']), 6)
            keyword_only = set(trace['channels']['keyword']) - set(trace['channels']['vector_pool'])
            self.assertTrue(keyword_only & set(trace['returned_chunk_ids']))
            target = next(row for row in rows if row['chunk_id'] in keyword_only)
            self.assertTrue(target['source_location'].startswith('page:2;'))
            def claim(prompt):
                evidence = json.loads(prompt.split('\n', 1)[1])['evidence']
                item = next(x for x in evidence if x['chunk_id'] == target['chunk_id'])
                return json.dumps({'status': 'supported', 'claims': [{'chunk_id': item['chunk_id'], 'quote': 'Order count: 48'}]})
            # Bind the same K for the existing citation chain.
            original = search.search
            search.search = lambda *a, **k: original(*a, **(k | {'top_k': 2}))
            answer = KnowledgeService(search, {}).answer('48', AgentRuntime(claim), today=date(2026, 11, 19), owner=owner, pdf_only=True)
            self.assertEqual(answer['citations'][0]['page_number'], 2)
            self.assertEqual(answer['citations'][0]['quote'], 'Order count: 48')
            self.assertEqual(len(reader.current_pdf_citations(answer['citations'], owner=owner)), 1)
            with patch.object(embedding, 'embed', return_value=[vector([0, 0, 1] + [0] * (DIMENSIONS - 3))]):
                empty, empty_trace = original('银行卡到账日期', today=date(2026, 11, 19), owner=owner, reviewed_pdf=True)
                self.assertEqual(empty, [])
                self.assertFalse(empty_trace['index_incompatible'])
            with reader.transaction(owner) as pg:
                raw = pg.execute("SELECT to_tsvector('simple','退款审核')::text AS raw, to_tsvector('simple',%s)::text AS segmented",
                                 (' '.join(keyword_terms('退款审核')),)).fetchone()
                self.assertIn('退款审核', raw['raw'])
                self.assertIn('退款', raw['segmented'])
                self.assertIn('审核', raw['segmented'])
            from test_business_knowledge import fixture_doc
            chinese = writer.index(fixture_doc(origin='hybrid-zh-'+token_hex(8), text='退款审核通过后站内信通知。'),
                                   embedding, owner=owner, external_approved=True)
            documents.append(chinese)
            _, chinese_trace = original('退款审核如何通知', today=date(2026, 11, 19), owner=owner, synthetic=True, rules=True)
            self.assertTrue(chinese_trace['channels']['keyword'])
            self.assertEqual(original('48', today=date(2026, 11, 19), owner=foreign, reviewed_pdf=True)[0], [])
            if report_path := os.getenv('HYBRID_PROOF_REPORT'):
                Path(report_path).write_text(json.dumps({'mode': 'real PostgreSQL/OCR/PDF + fixed orthogonal vectors, not real Embedding',
                    'trace': trace, 'keyword_only': sorted(keyword_only), 'citations': answer['citations'],
                    'chinese_lexemes': raw, 'chinese_retrieval': chinese_trace}, ensure_ascii=False, indent=2))
        finally:
            for doc in documents:
                writer.delete(doc['document_id'], owner=owner)
        self.assertEqual(Retriever(reader, embedding).search('48', today=date(2026, 11, 19), owner=owner, reviewed_pdf=True)[0], [])
