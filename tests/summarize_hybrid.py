"""Rescore captured hybrid runs only. No model, database, or Golden mutation."""
from collections import Counter
from hashlib import sha256
import json
from math import ceil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports/warehouse'


def summarize():
    data = {name: json.loads((REPORTS / filename).read_text()) for name, filename in {
        'before': 'hybrid_routing_before_fixed_v1.json', 'after': 'hybrid_routing_after_fixed_v1.json',
        'live': 'hybrid_candidate_live_v1.json', 'baseline_joint': 'hybrid_baseline_joint_v1.json',
        'final_joint': 'hybrid_final_joint_v1.json', 'proof': 'hybrid_keyword_proof_v1.json'}.items()}
    def score(values):
        applicable = [v for v in values if v is not None]
        return {'numerator': sum(applicable), 'denominator': len(applicable), 'not_applicable': len(values)-len(applicable)}
    routing = {}
    for name in ('before', 'after', 'live'):
        cases = data[name]['cases']
        routing[name] = {'task': score([c['passed'] for c in cases]),
            'route': score([c['answer']['task'] == c['golden']['route'] if 'route' in c['golden'] else None for c in cases]),
            'by_category': {category: score([c['passed'] for c in cases if c['golden'].get('route', 'time_conflict') == category])
                            for category in sorted({c['golden'].get('route', 'time_conflict') for c in cases})}}
    live = data['live']
    planning = [c for c in live['cases'] if any(a['stage'] == 'planning' for a in c['answer']['trace']['model_attempts'])]
    routing['live']['plan_validation'] = score([c['answer']['trace']['routing']['plan_validation']['status'] == 'ready' for c in planning])
    retrieval = {}
    for method in ('vector', 'hybrid'):
        rows = [r for r in live['retrieval'] if r['method'] == method]
        hits, rr = [], []
        for r in rows:
            golden, trace = r['golden'], r['answer']['retrieval_trace']
            # Frozen evaluation K=2; the existing answer service requests up to 6. Never score the full pool.
            ids = trace['returned_chunk_ids'][:2]
            locations = {c['chunk_id']: c['source_location'] for c in trace['candidates']}
            rank = next((i for i, cid in enumerate(ids, 1) if locations[cid].startswith(f"page:{golden['page']};")), None) if golden.get('page') else None
            hits.append(rank is not None if golden.get('page') else None)
            rr.append(1/rank if rank else 0) if golden.get('page') else None
        positives = [r for r in rows if r['golden'].get('page')]
        negatives = [r for r in rows if not r['golden'].get('page')]
        retrieval[method] = {'recall_at_2': score(hits), 'mrr_at_2': {'numerator': sum(rr), 'denominator': len(rr), 'not_applicable':len(rows)-len(rr)},
            'citation_support': score([r['support'] for r in positives]),
            'absence_owner_withdrawal': score([r['answer'].get('evidence_status') == 'insufficient' and not r['answer'].get('citations') for r in negatives]),
            'note': 'One labeled PDF-page evidence unit per positive query; negative queries are not recall misses. Tiny two-chunk live corpus.'}
    attempts = live['generation_events'] + [a for phase in ('baseline_joint', 'final_joint') for a in data[phase]['answer']['trace']['model_attempts']]
    elapsed = sorted(c['elapsed_ms'] for c in live['cases'])
    result = {'baseline_commit':'77f86db', 'dataset_sha256':sha256((ROOT/'tests/hybrid_cases_v1.json').read_bytes()).hexdigest(),
        'routing':routing, 'retrieval':retrieval,
        'real_model_adapter_calls':len(attempts), 'real_embedding_requests':sum(len(data[p]['embedding_batches']) for p in ('live','baseline_joint','final_joint')),
        'usage':{k:sum(a.get('usage',{}).get(k) or 0 for a in attempts) for k in ('prompt','output','total')},
        'provider_errors':dict(Counter(str(a.get('http_status')) for a in attempts if a['status']=='error')),
        'live_latency_ms':{'p50':elapsed[ceil(.5*len(elapsed))-1], 'p95':elapsed[ceil(.95*len(elapsed))-1]},
        'embedding_tokens':None, 'next_priority':'Grounded normalization of optional leading 从 in date-window proposals, retaining Validator and ambiguity checks.',
        'source_sha256':{str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in (
            ROOT/'src/dataquery_agent/rag/retrieval.py',ROOT/'src/dataquery_agent/rag/store.py',
            ROOT/'src/dataquery_agent/rag/routing.py', ROOT/'src/dataquery_agent/runtime_trace.py',
            ROOT/'src/dataquery_agent/api_models.py', ROOT/'src/dataquery_agent/conversation_manager.py',
            ROOT/'src/dataquery_agent/unified_agent.py',ROOT/'src/dataquery_agent/conversation_context.py')},
        'prompt_versions':{'intent':'intent-v3','resolution':'metric-resolution-v2', 'planning':'business-plan-v1','knowledge':'knowledge-extractive-v1'}}
    return result


if __name__ == '__main__':
    report = summarize()
    (REPORTS/'hybrid_summary_v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
