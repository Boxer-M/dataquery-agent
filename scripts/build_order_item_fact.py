"""Build and verify the first DWD fact, then verify a second execution adds nothing."""
import json
from datetime import datetime, timezone

import psycopg
from dotenv import dotenv_values
from dataquery_agent.dwd import build_order_item_fact
from dataquery_agent.metrics import ROOT


def main():
    env = dotenv_values(ROOT/'.env')
    with psycopg.connect(host='127.0.0.1',port=5433,dbname='sqlshed',autocommit=True,
                          user=env['POSTGRES_USER'],password=env['POSTGRES_PASSWORD']) as pg:
        first = build_order_item_fact(pg)
        repeated = build_order_item_fact(pg)
    first_action, repeat_action = first.pop('action'), repeated.pop('action')
    if first != repeated or repeat_action != 'verified_existing':
        raise ValueError('Repeat execution changed the fact or its validation results')
    report = dict(executed_at_utc=datetime.now(timezone.utc).isoformat(),
                  database='sqlshed', table='dwd.order_item_fact',
                  first_action=first_action, repeat_action=repeat_action, validation=first)
    (ROOT/'reports/dwd_order_item_fact.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
