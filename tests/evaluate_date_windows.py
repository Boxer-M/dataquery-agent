"""Explicit-date acceptance through the real HTTP/Planner path, with independent ODS SQL."""
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
from types import SimpleNamespace
from uuid import uuid4

from dotenv import dotenv_values
from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.conversation_store import owner_hash
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_integrated_e2e import stage_pdf
from evaluate_agent_e2e import safe_generate
from evaluate_business_planning import reference_connection
from test_api import AUTH, TOKEN, client

FIXTURE = ROOT / 'tests/date_window_cases_v1.json'


async def run(mode, phase, out):
    frozen = json.loads(FIXTURE.read_text())
    manager, _ = local_manager()
    manager.agent.supervisor = False
    owner = token_hex(32)
    backend = manager.generate
    embedding = MeasuredEmbedding.local()
    writer = KnowledgeStore.local(admin=True)
    report = {'phase': phase, 'mode': mode, 'baseline_commit': 'fc269bd',
              'generation_model_configured': dotenv_values(ROOT / '.env').get('GEMINI_MODEL'),
              'embedding_model': embedding.model, 'dataset_sha256': sha256(FIXTURE.read_bytes()).hexdigest(),
              'cases': [], 'generation_events': [], 'embedding_batches': embedding.batches, 'errors': []}
    def save():
        out.write_text(json.dumps(report,ensure_ascii=False,default=str,indent=2)+'\n')
    def model(prompt):
        if len(report['generation_events']) >= 12:
            raise RuntimeError('evaluation_call_budget')
        event = {'stage': prompt.split(']',1)[0].lstrip('['), 'mode': mode}
        report['generation_events'].append(event)
        began = monotonic()
        try:
            if mode == 'live':
                value = backend(prompt)
            else:
                value = {'text': json.dumps(frozen['recorded_proposal']), 'model': 'fixed', 'usage': {}}
            event.update(status='ok',model=value.get('model'),usage=value.get('usage', {}))
            return value
        except Exception as error:
            event.update(status='error',error=type(error).__name__,http_status=getattr(error,'code',None))
            raise
        finally:
            event['elapsed_ms'] = round((monotonic()-began)*1000)
    try:
        with ExitStack() as cleanup, reference_connection() as pg:
            pg.execute('SET TRANSACTION READ ONLY')
            pg.execute("SET LOCAL statement_timeout='15s'")
            document = SimpleNamespace(passages=())
            if phase == 'after' and mode == 'live':
                did, document = stage_pdf(writer, embedding, owner, cleanup)
                report['source'] = {'document_id':did,'hash':document.content_hash,'version':document.version}
                manager.agent.knowledge.retriever = Retriever(KnowledgeStore.local(), embedding)
            captured = []
            original = manager.process_message
            def capture(*args, **kwargs):
                answer = original(*args, **kwargs, today=date(2025,3,10))
                captured.append(answer)
                return answer
            manager.process_message = capture
            execute = manager.agent.service.execute
            sql_count = [0]
            def counted(*args, **kwargs):
                sql_count[0] += 1
                return execute(*args, **kwargs)
            manager.agent.service.execute = counted
            app = create_app(token=TOKEN, manager_factory=lambda:(manager,owner))
            cases = [{'id':'historical','question':frozen['question']}]
            if phase == 'after' and mode == 'live':
                cases.append({'id':'joint','question':'2025年1月订单量是多少，并引用PDF扫描页的 Order count 原文。'})
            async with client(app) as http:
                for case in cases:
                    outputs = []
                    joint = ['2025年1月订单量是多少','PDF扫描页的 Order count 原文。'] if case['id']=='joint' else None
                    manager.generate = safe_generate(model,outputs,document,case['question'],joint=joint)
                    cid = (await http.post('/api/conversations',headers=AUTH)).json()['conversation_id']
                    before = sql_count[0]
                    begin = monotonic()
                    response = await http.post('/api/chat',headers=AUTH,json={'conversation_id':cid,
                        'message_id':str(uuid4()),'question':case['question']})
                    answer = captured[-1]
                    result = response.json()
                    saved = await http.get(f"/api/queries/{answer['query_id']}",headers=AUTH)
                    checks = {'completed': result['status']=='completed', 'history_trace':saved.json()['trace']==result['trace']}
                    expected = {}
                    if case['id']=='historical':
                        rows = pg.execute('''SELECT p.category_id::text AS category,
                          sum(i.quantity*i.unit_price) AS item_gross_sales_amount,
                          sum(i.discount_amount) AS item_discount_amount
                          FROM ods.order_items i JOIN ods.orders o USING(order_id)
                          JOIN ods.products p USING(product_id)
                          WHERE o.ordered_at >= %s AND o.ordered_at < %s GROUP BY p.category_id''',
                          ('2025-04-01','2025-07-01')).fetchall()
                        expected = {r['category']:{mid:r[mid] for mid in frozen['expected']['metric_ids']} for r in rows}
                        actual = {r['bucket']:r['metrics'] for r in result['rows']}
                        checks['category_and_two_columns'] = bool(actual) and set(actual)==set(expected) and all(
                          list(actual[c])==frozen['expected']['metric_ids'] and all(
                            Decimal(actual[c][mid]['value'])==expected[c][mid] for mid in expected[c]) for c in expected)
                        plan = answer['result'].get('plan',{})
                        checks['window_and_grain'] = plan.get('group_by')==['category'] and plan.get('time_windows')==[
                            {'start_date':'2025-04-01','end_date':'2025-07-01'}]
                    else:
                        expected['order_count'] = pg.execute('SELECT count(*) FROM ods.orders WHERE ordered_at >= %s AND ordered_at < %s',
                                                          ('2025-01-01','2025-02-01')).fetchone()['count']
                        actual = result['rows'][0]['metrics']['order_count']['value'] if result['rows'] else None
                        checks['ods_order_count'] = actual==str(expected['order_count'])=='2312'
                        citations = answer['result'].get('knowledge_result',{}).get('citations',[])
                        valid = manager.agent.knowledge.retriever.store.current_pdf_citations(citations,owner=owner)
                        checks['pdf'] = any(c['page_number']==2 and c['quote']=='Order count: 48' for c in valid)
                    report['cases'].append({'id':case['id'],'question':case['question'],'http_status':response.status_code,
                        'response':result,'answer':answer,'model_outputs':outputs,'checks':checks,
                        'business_metric_sql':sql_count[0]-before,'expected_ods':expected,
                        'elapsed_ms':round((monotonic()-begin)*1000)})
                    save()
    except Exception as error:
        report['errors'].append({'type':type(error).__name__,'http_status':getattr(error,'code',None)})
    finally:
        save()
        with manager.store.transaction() as pg:
            pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s',(owner_hash(owner),))
    print(json.dumps({'phase':phase,'mode':mode,'cases':[(c['id'],c['checks'],c['business_metric_sql']) for c in report['cases']],
                      'errors':report['errors']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('fixed','live'),default='fixed')
    parser.add_argument('--phase',choices=('before','after'),required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(run(args.mode,args.phase,args.out))
