"""Batch A: nine explicit SQL models, one transaction, no destructive refresh."""
import hashlib
import json
import logging
import re
from pathlib import Path

from psycopg import sql
from psycopg.errors import QueryCanceled
from .dwd import build_transaction, materialize_relation, relation_identifier, validate_relation
from .metrics import ROOT, read_queries, window_parameters

MANIFEST = ROOT/'warehouse/models.json'
BASELINE = ROOT/'reports/ecommerce_metrics_v1.json'
SOURCE_KEYS = dict(categories='category_id', customers='customer_id', products='product_id',
                   orders='order_id', order_items='order_item_id', payments='payment_id', returns='return_id')
LOGGER = logging.getLogger(__name__)


def load_models(path=MANIFEST):
    manifest = json.loads(Path(path).read_text())
    models = manifest['models']
    seen = set()
    for model in models:
        name = model['name']
        if not re.fullmatch(r'(dim|dwd)\.[a-z_]+', name) or name in seen:
            raise ValueError('Invalid or duplicate model name')
        if not set(model['depends_on']) <= seen:
            raise ValueError(f'Missing or unordered dependency: {name}')
        if model['key'] not in {c['name'] for c in model['columns']}:
            raise ValueError(f'Missing primary key column: {name}')
        for kind in ('ddl','transform'):
            file = (ROOT/model[kind]).resolve()
            if not file.is_relative_to(ROOT) or not file.is_file():
                raise ValueError(f'Invalid SQL path: {name}')
        seen.add(name)
    return manifest


def content_fingerprint(pg, name, key):
    """Hash ordered full rows without exposing customer fields in reports."""
    digest = hashlib.sha256()
    with pg.cursor() as cur:
        cur.execute(sql.SQL('SELECT * FROM {} ORDER BY {}').format(
            relation_identifier(name), sql.Identifier(key)))
        while rows := cur.fetchmany(2000):
            for row in rows:
                digest.update((json.dumps(row, default=str, ensure_ascii=False, separators=(',',':'))+'\n').encode())
    return digest.hexdigest()


def source_fingerprints(pg):
    return {f'ods.{name}': content_fingerprint(pg, f'ods.{name}', key) for name, key in SOURCE_KEYS.items()}


def query_rows(pg, query):
    cur = pg.execute(query)
    names = [c.name for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def validate_sources(pg, baseline):
    profile = {name: query_rows(pg, query) for name, query in
               read_queries(ROOT/'sql/metrics/ecommerce_profile.sql').items()}
    rows = {r['table_name']: r['rows'] for r in profile['keys']}
    if set(rows) != set(SOURCE_KEYS) or any(r['invalid_keys'] for r in profile['keys']):
        raise ValueError('Q0: source keys or source table inventory')
    if any(r['orphans'] for r in profile['orphans']):
        raise ValueError('Q0: source orphan foreign keys')
    if any(r['nulls'] or r['nonfinite'] for r in profile['money']):
        raise ValueError('Q0: missing or nonfinite source money')
    if any(r['nonfinite'] for r in profile['event_times']):
        raise ValueError('Q0: nonfinite source time')
    fields = {f"{r['table_name']}.{n}": field for r in profile['columns'] for n, field in r['fields'].items()}
    if any(r['nulls'] and fields[r['field']]['nullable']=='NO' for r in profile['event_times']):
        raise ValueError('Q0: required source time missing')
    if any((fields[r['field']]['precision'], fields[r['field']]['scale']) != (12,2) for r in profile['money']):
        raise ValueError('Q0: source money precision')
    if baseline:
        expected_rows = {r['table_name']:r['rows'] for r in baseline['profile']['keys']}
        if rows != expected_rows or profile['columns'] != baseline['profile']['columns']:
            raise ValueError('Q0: approved source snapshot schema/count changed')
    checks = read_queries(ROOT/'sql/warehouse/quality/batch_a.sql')
    if pg.execute(checks['category_cycles']).fetchone()[0]:
        raise ValueError('Q0: category hierarchy cycle')
    lo, hi, days = pg.execute(checks['date_bounds']).fetchone()
    if days is None or not 0 < days <= 36600:
        raise ValueError('Q0: empty or excessive date range; review rather than truncate')
    return dict(rows=rows, date_min=str(lo), date_max=str(hi), date_days=days)


def metric_results(pg, query, params):
    cur = pg.execute(query, params)
    return dict(zip([c.name for c in cur.description], cur.fetchone()))


def validate_metrics(pg, baseline):
    source = read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')
    target = read_queries(ROOT/'sql/warehouse/quality/metrics.sql')
    windows = (baseline or json.loads(BASELINE.read_text()))['windows']
    report = {}
    for name, window in windows.items():
        LOGGER.info('A: checking eight metrics for %s', name)
        params = window_parameters(window['start'], window['end'], window['as_of'])
        results = {}
        for metric_id, query in source.items():
            try:
                expected = metric_results(pg, query, params)
                actual = metric_results(pg, target[metric_id], params)
            except QueryCanceled as error:
                raise ValueError(f'Metric query timed out: {name}/{metric_id}') from error
            if actual != expected:
                raise ValueError(f'Q0: DWD/ODS metric mismatch: {name}/{metric_id}')
            results[metric_id] = actual
        values = json.loads(json.dumps({k:r['value'] for k,r in results.items()}, default=str))
        if baseline and values != window['values']:
            raise ValueError(f'Q0: approved eight-metric baseline changed: {name}')
        report[name] = dict(start=window['start'], end=window['end'], as_of=window['as_of'],
                            results=results, ods_dwd_equal=True, approved_baseline_checked=bool(baseline))
    return report


def build_batch_a(pg, *, baseline_path=BASELINE, check_only=False):
    manifest = load_models()
    baseline = json.loads(Path(baseline_path).read_text()) if baseline_path else None

    def operation():
        if check_only:
            pg.execute('SET TRANSACTION READ ONLY')
        source = validate_sources(pg, baseline)
        LOGGER.info('A: source integrity checked; hashing original rows')
        before = source_fingerprints(pg)
        item = next(m for m in manifest['models'] if m['name']=='dwd.order_item_fact')
        if not pg.execute('SELECT to_regclass(%s)', (item['name'],)).fetchone()[0]:
            raise ValueError('Required existing order_item_fact absent; batch never rebuilds it')
        validate_relation(pg, item, (ROOT/item['transform']).read_text())
        item_before = content_fingerprint(pg, item['name'], item['key'])
        results = {}
        for model in manifest['models']:
            projection = (ROOT/model['transform']).read_text()
            if check_only:
                result = validate_relation(pg, model, projection)
                result['action'] = 'verified_existing'
            else:
                result = materialize_relation(pg, model, projection, (ROOT/model['ddl']).read_text())
            source_table = model['source_table']
            expected = source['rows'][source_table.split('.')[1]] if source_table else None
            if expected is not None and result['rows'] != expected:
                raise ValueError(f"Q0: model/source grain mismatch: {model['name']}")
            results[model['name']] = result
            LOGGER.info('A: %s %s, %s rows', model['name'], result['action'], result['rows'])
        if results['dim.date']['rows'] != source['date_days']:
            raise ValueError('Q0: date coverage mismatch')
        checks = read_queries(ROOT/'sql/warehouse/quality/batch_a.sql')
        links = dict(pg.execute(checks['dimension_links']).fetchall())
        if any(links.values()) or pg.execute(checks['date_links']).fetchone()[0]:
            raise ValueError('Q0: fact/dimension/date association mismatch')
        if pg.execute(checks['source_return_flags']).fetchone() != pg.execute(checks['target_return_flags']).fetchone():
            raise ValueError('Q0: return anomaly flags differ from independent ODS check')
        metrics = validate_metrics(pg, baseline)
        if before != source_fingerprints(pg) or item_before != content_fingerprint(pg, item['name'], item['key']):
            raise ValueError('Q0: source or existing item fact changed')
        cancelled = pg.execute(checks['cancelled_items']).fetchone()
        return dict(model_version=manifest['model_version'], source=source, models=results,
                    source_fingerprints=before,
                    source_snapshot_id=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
                    existing_item_fingerprint=item_before, source_and_existing_item_unchanged=True,
                    relationship_orphans=links, date_orphans=0, metrics=metrics,
                    cancelled_item_rows=cancelled[0], cancelled_item_amount=cancelled[1],
                    payment_statuses=query_rows(pg, checks['payment_statuses']))
    return build_transaction(pg, operation)
