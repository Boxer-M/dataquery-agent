"""Manual, budgeted evaluation of the existing 13-case public Golden set."""
import json
import os
from pathlib import Path
from secrets import token_hex
from time import monotonic
from uuid import uuid4
from unittest.mock import patch

from bootstrap import bootstrap
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.documents import parse_file
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_agent_e2e import GOLDEN, TODAY, FIXTURES, safe_generate, rescore, reference_connection
from benchmark.adapters import snapshot, live_environment
from benchmark.dataset import digest
from benchmark.live_baseline_budget import Budget, BaselineEmbedding, generator, LIMITS


def evaluate():
    if not os.getenv('CI_GEMINI_KEY') or os.getenv('CI_NO_EXTERNAL_MODELS') != '0':
        raise RuntimeError('Manual authorization and Gemini secret required')
    bootstrap()
    destination = Path('artifacts')
    destination.mkdir(exist_ok=True)
    manager, _ = local_manager()
    environment = live_environment()
    budget = Budget(environment['configured_model'])
    embedding = BaselineEmbedding(os.environ['CI_GEMINI_KEY'], budget)
    doc = parse_file(FIXTURES / 'knowledge/refund_fixture.md', title='测试星店退款规则',
                     source_type='synthetic_fixture', source_uri='fixture:refund_fixture.md', topic='refund_fixture', version='1')
    embedding.approved_documents.update('\n'.join([doc.title, *p.heading_path, p.text]) for p in doc.passages)
    blocked = []
    backend = generator(manager.generate, budget, {(doc.title, p.text) for p in doc.passages}, blocked)
    owner = token_hex(32)
    writer = KnowledgeStore.local(admin=True)
    rows, conversations, document_id = [], {}, None
    report = {'mode': 'real_gemini', 'revision': os.environ.get('CI_SOURCE_SHA'), 'golden_version': GOLDEN['version'],
              'golden_sha256': digest(GOLDEN), 'model_config': environment, 'cases': rows,
              'catalog_index': 'fixed vectors for deterministic exact lookup only',
              'rag_index': 'real Gemini Embedding of the authorized synthetic refund fixture',
              'budget': dict(LIMITS), 'api_events': budget.events}
    try:
        document_id = writer.index(doc, embedding, owner=owner, external_approved=True)['document_id']
        manager.agent.knowledge = KnowledgeService(Retriever(KnowledgeStore.local(), embedding),
                                                  manager.agent.service.metrics, synthetic=True)
        with reference_connection() as pg:
            report['database_snapshot'] = snapshot(pg)
        with reference_connection() as pg:
            pg.execute('SET TRANSACTION READ ONLY')
            for case in GOLDEN['cases']:
                cid = conversations[case['after']] if 'after' in case else manager.create_conversation(owner)['conversation_id']
                conversations[case['id']] = cid
                outputs = []
                manager.generate = safe_generate(backend, outputs, doc, case['question'])
                started = monotonic()
                answer = manager.process_message(owner, cid, case['question'], uuid4(), today=TODAY)
                history = manager.get_query(owner, answer['query_id']) if 'query_id' in answer else None
                rows.append({'golden': case, 'elapsed_ms': round((monotonic() - started) * 1000),
                             'answer': answer, 'history': history, 'model_outputs': outputs})
                (destination / 'live-progress.json').write_text(json.dumps(report, ensure_ascii=False, default=str))
            rescore(report, pg)
        # Same case IDs, different code/model states: descriptive comparison, without causal claims.
        baseline = json.loads(Path('reports/warehouse/agent_e2e_evaluation_v1.json').read_text())['baseline']
        previous = {r['golden']['id']: r for r in baseline['cases']}
        report['historical_comparison'] = {
            'baseline_revision': baseline['revision'], 'same_run_conditions': False,
            'cases': [{'id': r['golden']['id'], 'before': previous.get(r['golden']['id'], {}).get('passed'),
                       'after': r['passed'], 'failure': r['failure']} for r in rows]}
        print('Golden evaluation completed; stochastic scores are informational')
    finally:
        if document_id:
            writer.delete(document_id, owner=owner)
        report['blocked_calls'] = blocked
        (destination / 'live-report.json').write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2))


if __name__ == '__main__':
    with patch.dict(LIMITS, generation_calls=40, embedding_calls=10, reserved_tokens=4_000_000, reserved_usd=5.0):
        evaluate()
