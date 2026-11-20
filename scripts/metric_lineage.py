"""Local reviewed graph import/query. Never accepts arbitrary Cypher."""
import argparse
import json
import os
from secrets import token_hex

from dataquery_agent.metrics import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init-config','import','query'))
    parser.add_argument('--field', default='ods.order_items.discount_amount')
    args = parser.parse_args()
    if args.command == 'init-config':
        password=token_hex(32)
        with os.fdopen(os.open(ROOT/'.env.neo4j',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as out:
            out.write(f'NEO4J_AUTH=neo4j/{password}\nNEO4J_HTTP_URL=http://127.0.0.1:7474\n')
        print('Created ignored .env.neo4j with mode 600; no credentials printed.')
        return
    from dataquery_agent.metric_lineage import LineageTool
    from dataquery_agent.lineage_evidence import LineageError
    try:
        tool=LineageTool.local()
        result=tool.import_graph() if args.command=='import' else tool.lookup(args.field.strip().lower())
    except (LineageError,OSError) as error:
        result={'status':'error','code':str(error) if isinstance(error,LineageError) else 'lineage_source_unavailable'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result.get('status')=='error' else 0


if __name__=='__main__':
    raise SystemExit(main())
