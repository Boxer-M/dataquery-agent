"""Read-only PostgreSQL validation; only --output writes a local report file."""
import argparse
import json
from datetime import datetime, timezone

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from dataquery_agent.metrics import ROOT, read_queries, window_parameters


def validate_profile(profile, spec):
    expected = dict(categories=24, customers=12000, products=2000, orders=90000,
                    order_items=215000, payments=95000, returns=12000)
    if {row['table_name'] for row in profile['keys']} != set(expected):
        raise ValueError('Incomplete source tables')
    for row in profile['keys']:
        if row['rows'] != expected[row['table_name']] or row['invalid_keys']:
            raise ValueError('Unexpected source count/keys')
    if any(row['orphans'] for row in profile['orphans']):
        raise ValueError('Orphan foreign key')
    fields = {f"ods.{r['table_name']}.{name}": info for r in profile['columns']
              for name, info in r['fields'].items()}
    for metric in spec['metrics']:
        for field in metric['source_fields']:
            if field not in fields:
                raise ValueError(f'Missing metric source field: {field}')
    for row in profile['money']:
        field = fields['ods.'+row['field']]
        if row['nulls'] or row['nonfinite'] or (field['precision'], field['scale']) != (12, 2):
            raise ValueError('Invalid required money/precision')
    for row in profile['event_times']:
        if row['nonfinite'] or (row['nulls'] and fields['ods.'+row['field']]['nullable'] == 'NO'):
            raise ValueError('Invalid required event time')
    enums = {'order_statuses': {'cancelled','delivered','processing','returned','shipped'},
             'payment_statuses': {'captured','failed'}}
    for name, allowed in enums.items():
        if {row['status'] for row in profile[name]} - allowed:
            raise ValueError('New status requires metric specification review')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=str)
    parser.add_argument('--check', action='store_true', help='Compare with committed baseline')
    args = parser.parse_args()
    spec = json.loads((ROOT/'metrics/ecommerce_v1.json').read_text())
    queries = read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')
    if set(queries) != {m['metric_id'] for m in spec['metrics']}:
        raise ValueError('Specification / SQL identifiers differ')
    env = dotenv_values(ROOT/'.env')
    report = {'version': spec['version'], 'executed_at_utc': datetime.now(timezone.utc).isoformat()}
    with psycopg.connect(host='127.0.0.1', port=5433, dbname='sqlshed',
                          user=env['POSTGRES_USER'], password=env['POSTGRES_PASSWORD'],
                          options='-c default_transaction_read_only=on', row_factory=dict_row) as pg:
        pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        pg.execute("SET LOCAL statement_timeout = '30s'")
        report['transaction_read_only'] = pg.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
        report['profile'] = {name: pg.execute(query).fetchall() for name, query in
                             read_queries(ROOT/'sql/metrics/ecommerce_profile.sql').items()}
        validate_profile(report['profile'], spec)
        report['windows'] = {}
        for name, start, end, as_of in [
            ('full', '2023-01-01', '2026-01-01', '2026-01-01'),
            ('january_2025', '2025-01-01', '2025-02-01', '2026-01-01'),
            ('empty', '2030-01-01', '2030-02-01', '2030-02-01')]:
            params = window_parameters(start, end, as_of)
            results = {key: pg.execute(query, params).fetchone() for key, query in queries.items()}
            values = {key: row['value'] for key, row in results.items()}
            components = {key: {'numerator': row['numerator'], 'denominator': row['denominator']}
                          for key, row in results.items() if 'denominator' in row}
            report['windows'][name] = dict(start=start, end=end, as_of=as_of, values=values, components=components)
    serialized = json.loads(json.dumps(report, default=str))
    if args.check:
        baseline = json.loads((ROOT/'reports/ecommerce_metrics_v1.json').read_text())
        for key in ('version', 'transaction_read_only', 'windows', 'profile'):
            if serialized[key] != baseline[key]:
                raise ValueError(f'Baseline mismatch: {key}')
    if args.output:
        (ROOT/args.output).write_text(json.dumps(serialized, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(serialized['windows'], ensure_ascii=False))


if __name__ == '__main__':
    main()
