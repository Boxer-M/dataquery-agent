"""Build all batch-A DWD/DIM models; never refresh or overwrite existing data."""
import argparse
import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone

import psycopg
from dotenv import dotenv_values
from dataquery_agent.metrics import ROOT
from dataquery_agent.warehouse import build_batch_a, content_fingerprint, load_models


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def olist_snapshot(config):
    with psycopg.connect(**config, dbname='olist', options='-c default_transaction_read_only=on') as pg:
        pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        return dict(orders=pg.execute('SELECT count(*) FROM ods.orders').fetchone()[0],
                    orders_sha256=content_fingerprint(pg, 'ods.orders', 'order_id'))


def artifact_hashes():
    paths = ['warehouse/models.json', 'metrics/ecommerce_v1.json', 'sql/metrics/ecommerce_v1.sql',
             'sql/metrics/ecommerce_profile.sql', 'src/dataquery_agent/dwd.py',
             'src/dataquery_agent/warehouse.py', 'scripts/build_warehouse.py']
    paths += [str(p.relative_to(ROOT)) for p in (ROOT/'sql/warehouse').rglob('*.sql')]
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Print the model order without connecting')
    parser.add_argument('--check-only', action='store_true', help='Read-only validation; missing targets fail')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    if args.plan:
        print('\n'.join(m['name'] for m in load_models()['models']))
        return
    env = dotenv_values(ROOT/'.env')
    config = dict(host='127.0.0.1',port=5433,user=env['POSTGRES_USER'],password=env['POSTGRES_PASSWORD'])
    before = olist_snapshot(config)
    approved_olist = json.loads((ROOT/'reports/dwd_order_item_fact_safety.json').read_text())['olist_orders']
    if before['orders'] != approved_olist:
        raise ValueError('Olist orders differ from the previously accepted baseline')
    with psycopg.connect(**config, dbname='sqlshed', autocommit=True) as pg:
        first = build_batch_a(pg, check_only=args.check_only)
        repeated = build_batch_a(pg, check_only=True)
    actions = {name:result.pop('action') for name,result in first['models'].items()}
    repeat_actions = {name:result.pop('action') for name,result in repeated['models'].items()}
    if first != repeated or set(repeat_actions.values()) != {'verified_existing'}:
        raise ValueError('Repeated batch validation changed results')
    after = olist_snapshot(config)
    if before != after:
        raise ValueError('Olist order content changed; no successful report published')
    artifacts = artifact_hashes()
    build_key = hashlib.sha256(json.dumps(dict(source=first['source_snapshot_id'],artifacts=artifacts),sort_keys=True).encode()).hexdigest()
    raw_manifest = ROOT/'data/raw/sqlshed/download_manifest.json'
    report = dict(status='accepted_batch_a', build_key=build_key, release_id='batch_a_'+build_key,
                  executed_at_utc=datetime.now(timezone.utc).isoformat(),
                  tested_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  tested_artifact_sha256=artifacts,
                  raw_file_manifest_sha256=hashlib.sha256(raw_manifest.read_bytes()).hexdigest() if raw_manifest.exists() else None,
                  first_actions=actions, repeat_actions=repeat_actions, repeat_verified=True,
                  olist_before=before, olist_after=after, validation=first,
                  boundaries=['DWS/ADS not built','22 extended metrics not validated','8 unsupported metrics unchanged'])
    destination = ROOT/f'reports/warehouse/{report["release_id"]}.json'
    if not destination.exists():
        atomic_json(destination, report)
    elif json.loads(destination.read_text())['build_key'] != build_key:
        raise ValueError('Existing release report collision; refusing overwrite')
    atomic_json(ROOT/'reports/warehouse/current.json',dict(release_id=report['release_id'],report=str(destination.relative_to(ROOT))))
    print(json.dumps(dict(report=str(destination.relative_to(ROOT)),
        rows={n:r['rows'] for n,r in first['models'].items()},first_actions=actions,
        repeat_verified=True,olist_orders=after['orders']),ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        atomic_json(ROOT/'data/staging/warehouse/last_failure.json',dict(status='failed',error_type=type(error).__name__))
        raise
