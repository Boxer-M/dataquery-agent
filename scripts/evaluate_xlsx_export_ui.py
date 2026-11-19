"""Explicit fixed-proposal local API for browser export acceptance; no new HTTP endpoints."""
import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
import uvicorn

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.metrics import ROOT
from dataquery_agent.performance import Performance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixed-proposal', action='store_true')
    parser.add_argument('--stats', type=Path, required=True)
    args = parser.parse_args()
    if not args.fixed_proposal:
        parser.error('Use --fixed-proposal for offline-model browser acceptance.')
    from test_business_query_planner import CASES, proposal_output
    manager, owner = local_manager()
    manager.agent.supervisor = False
    def no_embedding(*_args, **_kwargs):
        raise RuntimeError('Embedding is disabled in fixed-proposal browser acceptance')
    manager.agent.knowledge.retriever.embedding.embed = no_embedding
    stats = {'mode': 'fixed proposal + real PostgreSQL, cache off, Supervisor off',
             'business_metric_sql': 0, 'proposal_calls': 0, 'real_gemini_calls': 0}
    def save():
        args.stats.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
        args.stats.chmod(0o600)
    execute = manager.agent.service.execute
    def counted_sql(*arguments, **keywords):
        stats['business_metric_sql'] += 1  # This acceptance query has one scalar window per call.
        save()
        return execute(*arguments, **keywords)
    def counted_model(prompt):
        if not prompt.startswith('[business-plan-v1]') or stats['proposal_calls'] >= 1:
            raise RuntimeError('Only the fixed multi-metric planning fixture is enabled')
        stats['proposal_calls'] += 1
        save()
        return proposal_output(CASES['multi'])
    manager.agent.service.execute = counted_sql
    manager.generate = counted_model
    save()
    app = create_app(token=dotenv_values(ROOT / '.env.api').get('DATAQUERY_HTTP_TOKEN'),
                     manager_factory=lambda: (manager, owner), performance=Performance(report=True))
    uvicorn.run(app, host='127.0.0.1', port=8000, proxy_headers=False, ws='none', access_log=False)


if __name__ == '__main__':
    main()
