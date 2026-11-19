"""Frozen hybrid evaluation: existing HTTP/Agent, ODS truth, scoped PDF, explicit live opt-in."""
import argparse
import asyncio
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_hex
from time import monotonic
from uuid import uuid4
from unittest.mock import Mock

from dotenv import dotenv_values
from dataquery_agent.api import create_app
from dataquery_agent.api_models import public_response
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import owner_hash
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_business_knowledge import FixedEmbedding
from evaluate_business_planning import reference_connection, reference_rows
from evaluate_integrated_e2e import stage_pdf
from evaluate_agent_e2e import safe_generate
from test_api import AUTH, TOKEN, client
from test_business_planner_v2 import PROPOSE

CASES = ROOT / 'tests/hybrid_cases_v1.json'
TODAY = date(2025, 3, 10)


def fixed(prompt):
    if prompt.startswith('[business-plan-v1]'):
        if 'PDF扫描页' in prompt:
            return json.dumps(PROPOSE | {'metric_ids': ['order_count'], 'pdf_question': 'PDF扫描页的 Order count 原文。'})
        return json.dumps(PROPOSE | {'metric_ids': ['item_gross_sales_amount', 'item_discount_amount'],
                                    'time_windows': ['从2025-04-01至2025-06-30'], 'group_by': ['按品类']})
    if prompt.startswith('[knowledge-extractive-v1]'):
        data = json.loads(prompt.split('\n', 1)[1])
        quote = 'Order count: 24' if 'Native verification' in data['question'] else 'Order count: 48'
        if '银行卡' in data['question']:
            return json.dumps({'status': 'insufficient', 'claims': []})
        item = next((x for x in data['evidence'] if quote in x['text']), None)
        return json.dumps({'status': 'supported', 'claims': [{'chunk_id': item['chunk_id'], 'quote': quote}]}
                          if item else {'status': 'insufficient', 'claims': []})
    return json.dumps({'metrics': ['订单量'], 'time': '2025年1月', 'groups': [], 'filters': [], 'type': 'metric', 'missing': []})


async def run(mode, out, baseline=False):
    golden = json.loads(CASES.read_text())
    manager, _ = local_manager()
    writer = KnowledgeStore.local(admin=True)
    owner, other = token_hex(32), token_hex(32)
    backend = manager.generate if mode == 'live' else lambda prompt: {'text': fixed(prompt), 'model': 'fixed', 'usage': {}}
    manager.agent.supervisor = False  # Compare the frozen Planner/tool path, not optional delegation.
    embedding = MeasuredEmbedding.local() if mode == 'live' else FixedEmbedding()
    if baseline:
        if mode != 'fixed':
            raise ValueError('baseline source replay is fixed-proposal only')
        # Reuse the existing trusted-Git evaluator, never execute model-generated code.
        from benchmark.routing_v2_fixed import oldmodule
        from unittest.mock import patch
        from dataquery_agent.rag import routing
        old_context = oldmodule('77f86db', 'dataquery_agent', 'src/dataquery_agent/conversation_context.py')
        old_manager = oldmodule('77f86db', 'dataquery_agent', 'src/dataquery_agent/conversation_manager.py')
        old_agent = oldmodule('77f86db', 'dataquery_agent', 'src/dataquery_agent/unified_agent.py')
        old_route = oldmodule('77f86db', 'dataquery_agent.rag', 'src/dataquery_agent/rag/routing.py')
        old_manager.edit_for_message = old_context.edit_for_message
        old_manager.ContextError = old_context.ContextError
        current = manager.agent
        agent = old_agent.UnifiedAgent(current.service, current.capability, current.detail_executor,
                                       current.dynamic_engine, current.asset_runtime, current.knowledge)
        manager = old_manager.ConversationManager(manager.store, agent, backend)
        route_patch = patch.object(routing, 'route_knowledge', old_route.route_knowledge)
    generator_events, retrieval_events = [], []
    report = {'mode': mode, 'baseline_source_replay': baseline, 'baseline_commit': golden['baseline_commit'], 'dataset_sha256': sha256(CASES.read_bytes()).hexdigest(),
              'generation_model_configured': dotenv_values(ROOT / '.env').get('GEMINI_MODEL'),
              'embedding_model': embedding.model, 'supervisor': False, 'cases': [], 'retrieval': [], 'errors': []}
    def save():
        out.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2) + '\n')
    def generate(prompt):
        if len(generator_events) >= 30 or sum(e.get('usage', {}).get('total') or 0 for e in generator_events) >= 90000:
            raise RuntimeError('evaluation_call_budget')
        event = {'stage': prompt.split(']', 1)[0].lstrip('['), 'kind': 'real' if mode == 'live' else 'fixed'}
        generator_events.append(event)
        start = monotonic()
        try:
            reply = backend(prompt)
            event.update(status='ok', model=reply.get('model') if isinstance(reply, dict) else 'fixed',
                         usage=reply.get('usage', {}) if isinstance(reply, dict) else {})
            return reply
        except Exception as error:
            event.update(status='error', error=type(error).__name__, http_status=getattr(error, 'code', None))
            raise
        finally:
            event['elapsed_ms'] = round((monotonic() - start) * 1000)
    try:
        with ExitStack() as cleanup, reference_connection() as pg:
            if baseline:
                cleanup.enter_context(route_patch)
            pg.execute('SET TRANSACTION READ ONLY')
            pg.execute("SET LOCAL statement_timeout='15s'")
            report['ods_snapshot'] = pg.execute('SELECT (SELECT count(*) FROM ods.orders) orders,(SELECT count(*) FROM ods.order_items) items').fetchone()
            did, document = stage_pdf(writer, embedding, owner, cleanup)
            report['source'] = {'document_id': did, 'sha256': document.content_hash, 'version': document.version,
                                'chunks': [{'location': p.location, 'sha256': sha256(p.text.encode()).hexdigest()} for p in document.passages]}
            manager.agent.knowledge.retriever = Retriever(KnowledgeStore.local(), embedding)
            app = create_app(token=TOKEN, manager_factory=lambda: (manager, owner))
            original = manager.process_message
            captured = []
            def capture(*args, **kwargs):
                answer = original(*args, **kwargs, today=TODAY)
                captured.append(answer)
                return answer
            manager.process_message = capture
            conversations = {}
            # Real SQL+PDF is deliberately first; subsequent ordering retains follow-up dependencies.
            order = sorted(golden['routing'], key=lambda c: c['id'] != 'joint')
            async with client(app) as http:
                for case in order:
                    outputs = []
                    clauses = ['2025年1月订单量是多少', 'PDF扫描页的 Order count 原文。'] if case['id'] == 'joint' else None
                    manager.generate = safe_generate(generate, outputs, document, case['question'], joint=clauses)
                    cid = conversations[case['after']] if 'after' in case else (await http.post('/api/conversations', headers=AUTH)).json()['conversation_id']
                    conversations[case['id']] = cid
                    started = monotonic()
                    response = await http.post('/api/chat', headers=AUTH, json={'conversation_id': cid,
                                              'message_id': str(uuid4()), 'question': case['question']})
                    answer = captured[-1]
                    history = manager.get_query(owner, answer['query_id'])
                    result = answer['result']
                    knowledge = result.get('knowledge_result', result)
                    checks = {'status': answer['status'] == case['status'],
                              'route': answer['task'] == case['route'] if 'route' in case else None,
                              'history_trace': (await http.get(f"/api/queries/{answer['query_id']}", headers=AUTH)).json().get('trace') == response.json().get('trace')}
                    expected, calls = {}, [t['request'] for t in answer['trace']['tools'] if 'request' in t]
                    for request in calls:
                        truth = reference_rows(pg, {'group_by': [], 'filters': {}} | request)
                        expected[request['metric_id']] = truth
                        metric = result.get('query_result') or next((r for r in result.get('metric_results', []) if r['metric_id'] == request['metric_id']), None)
                        if metric is None and 'results' in result:
                            actual = {r['bucket']: r['metrics'][request['metric_id']] for r in result['results']}
                        else:
                            actual = {r['bucket']: r for r in (metric or {}).get('results', [])}
                        checks['sql_' + request['metric_id']] = set(actual) == set(truth) and all(
                            (Decimal(str(actual[b]['value'])) if actual[b]['value'] is not None else None) == truth[b]['value'] for b in truth)
                    if 'metric' in case:
                        checks['request'] = bool(calls and calls[0]['metric_id'] == case['metric'] and calls[0]['start_date'] == ('2025-02-01' if case.get('month') == 2 else '2025-01-01'))
                        if case.get('channel'):
                            checks['channel'] = bool(calls and calls[0].get('filters', {}).get('channel') == case['channel'])
                    if 'page' in case:
                        citations = knowledge.get('citations', [])
                        valid = manager.agent.knowledge.retriever.store.current_pdf_citations(citations, owner=owner)
                        checks['pdf_support'] = any(c['page_number'] == case['page'] and case['quote'] in c['quote'] for c in valid)
                    report['cases'].append({'golden': case, 'http_status': response.status_code, 'checks': checks,
                        'passed': all(v is not False for v in checks.values()), 'answer': answer, 'model_outputs': outputs,
                        'ods': expected, 'elapsed_ms': round((monotonic() - started) * 1000)})
                    save()
            for case in golden['retrieval']:
                if case.get('withdrawn'):
                    writer.delete(did, owner=owner)
                for method in ('vector', 'hybrid'):
                    retriever = Retriever(KnowledgeStore.local(), embedding, mode=method)
                    manager.generate = safe_generate(generate, [], document, case['question'])
                    from dataquery_agent.agent_runtime import AgentRuntime
                    service = KnowledgeService(retriever, manager.agent.service.metrics)
                    began = monotonic()
                    answer = service.answer(case['question'], AgentRuntime(manager.generate), today=TODAY,
                                            owner=other if case.get('foreign') else owner, pdf_only=True)
                    trace = answer.get('retrieval_trace', {})
                    ids = trace.get('returned_chunk_ids', [])[:golden['top_k']]
                    targets = [x for x in trace.get('candidates', []) if x['chunk_id'] in ids]
                    hit = any(x['source_location'].startswith(f"page:{case['page']};") for x in targets) if case.get('page') else None
                    support = any(c['page_number'] == case['page'] and case['quote'] in c['quote'] for c in answer.get('citations', [])) if case.get('page') else (
                        answer.get('status') == 'ok' and answer.get('evidence_status') == 'insufficient' and not answer.get('citations'))
                    rank = next((i for i, cid in enumerate(ids, 1) if any(x['chunk_id'] == cid and x['source_location'].startswith(f"page:{case['page']};") for x in targets)), None) if case.get('page') else None
                    report['retrieval'].append({'golden': case, 'method': method, 'top_k': trace.get('top_k'), 'evaluation_k': golden['top_k'], 'hit': hit,
                        'reciprocal_rank': 1/rank if rank else (0 if hit is not None else None), 'support': support,
                        'answer': answer, 'elapsed_ms': round((monotonic() - began) * 1000)})
                    save()
    except Exception as error:
        report['errors'].append({'type': type(error).__name__, 'http_status': getattr(error, 'code', None)})
    finally:
        report['generation_events'] = generator_events
        report['embedding_batches'] = getattr(embedding, 'batches', [])
        report['real_generation_calls'] = len(generator_events) if mode == 'live' else 0
        report['real_embedding_calls'] = len(report['embedding_batches']) if mode == 'live' else 0
        save()
        with manager.store.transaction() as pg:
            pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s', (owner_hash(owner),))
    print(json.dumps({'mode': mode, 'route_passed': sum(c['passed'] for c in report['cases']), 'route_total': len(report['cases']), 'errors': report['errors']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('fixed', 'live'), default='fixed')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline', action='store_true', help='Fixed proposals against trusted 77f86db routing/context sources')
    args = parser.parse_args()
    asyncio.run(run(args.mode, args.out, args.baseline))
