"""Small shared PostgreSQL model primitives and compatible first-fact entry point."""
from decimal import Decimal

from psycopg import sql
from .metrics import ROOT, read_queries, window_parameters

MODEL = ROOT/'sql/dwd/order_item_fact.sql'
TYPES = dict(order_item_id='integer', order_id='integer', product_id='integer',
             quantity='integer', unit_price='numeric(12,2)', discount_amount='numeric(12,2)',
             net_item_amount='numeric', order_purchase_time='timestamp without time zone',
             order_status='character varying', dq_nonpositive_quantity='boolean',
             dq_negative_amount='boolean', dq_discount_exceeds_gross='boolean',
             dq_unknown_order_status='boolean', dq_invalid_order_time='boolean')


def relation_identifier(name):
    return sql.Identifier(*name.split('.'))


def validate_relation(pg, model, projection):
    """Exact schema, constraints, multiset and key checks; never writes an existing table."""
    checks = read_queries(ROOT/'sql/warehouse/quality/relation.sql')
    name = model['name']
    if pg.execute(checks['kind'], (name,)).fetchone() != ('r', False):
        raise ValueError(f'Unsupported target kind or RLS: {name}')
    expected = [(c['name'], c['type'], not c['nullable']) for c in model['columns']]
    if pg.execute(checks['columns'], (name,)).fetchall() != expected:
        raise ValueError(f'Target schema mismatch: {name}; refusing changes')
    actual = [r[0] for r in pg.execute(checks['constraints'], (name,)).fetchall()]
    if actual != sorted(model['constraints']):
        raise ValueError(f'Target constraints mismatch: {name}')
    if model.get('indexes'):
        indexes = {n:(definition, valid) for n, definition, valid in pg.execute(checks['indexes'], (name,)).fetchall()}
        for index in model['indexes']:
            definition = f"CREATE INDEX {index['name']} ON {name} USING btree ({', '.join(index['columns'])})"
            if indexes.get(index['name']) != (definition, True):
                raise ValueError(f'Required target index mismatch: {name}')
    differences = pg.execute(sql.SQL(checks['differences']).format(
        projection=sql.SQL(projection.strip().rstrip(';')), target=relation_identifier(name))).fetchone()[0]
    if differences:
        raise ValueError(f'Target/source full-row mismatch ({differences}): {name}; refusing overwrite')
    keys = model.get('keys', [model['key']])
    key_sql = sql.Identifier(keys[0]) if len(keys)==1 else sql.SQL('ROW({})').format(
        sql.SQL(',').join(sql.Identifier(k) for k in keys))
    rows, distinct, nulls = pg.execute(sql.SQL(checks['summary']).format(
        key=key_sql, key_nulls=sql.SQL(' OR ').join(
            sql.SQL('{} IS NULL').format(sql.Identifier(k)) for k in keys),
        target=relation_identifier(name))).fetchone()
    if rows != distinct or nulls:
        raise ValueError(f'Invalid target keys: {name}')
    flags = {}
    for col in model['columns']:
        if col['name'].startswith('dq_') and col['type']=='boolean':
            flags[col['name']] = pg.execute(sql.SQL(checks['flags']).format(
                target=relation_identifier(name), flag=sql.Identifier(col['name']))).fetchone()[0]
    return dict(rows=rows, unique_keys=distinct, null_keys=nulls, full_row_differences=differences,
                quality_flags=flags)


def materialize_relation(pg, model, projection, ddl):
    existing = pg.execute('SELECT to_regclass(%s)', (model['name'],)).fetchone()[0]
    if not existing:
        if model.get('reuse_only'):
            raise ValueError(f"Required existing table absent: {model['name']}")
        pg.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(model['name'].split('.')[0])))
        pg.execute(ddl)
        pg.execute(sql.SQL('INSERT INTO {} ({}) {}').format(
            relation_identifier(model['name']),
            sql.SQL(',').join(sql.Identifier(c['name']) for c in model['columns']),
            sql.SQL(projection.strip().rstrip(';'))))
        for index in model.get('indexes', []):
            pg.execute(sql.SQL('CREATE INDEX {} ON {} ({})').format(
                sql.Identifier(index['name']), relation_identifier(model['name']),
                sql.SQL(',').join(sql.Identifier(c) for c in index['columns'])))
        pg.execute(sql.SQL('ANALYZE {}').format(relation_identifier(model['name'])))
    report = validate_relation(pg, model, projection)
    report['action'] = 'verified_existing' if existing else 'created'
    return report


def build_transaction(pg, operation):
    if not pg.autocommit:
        raise ValueError('Use an autocommit connection for an independent build transaction')
    if not pg.execute('SELECT pg_try_advisory_lock(426025)').fetchone()[0]:
        raise ValueError('Another DWD build is active; no changes made')
    try:
        with pg.transaction():
            pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            pg.execute("SET LOCAL statement_timeout = '60s'")
            return operation()
    finally:
        pg.execute('SELECT pg_advisory_unlock(426025)')


def legacy_model():
    return dict(name='dwd.order_item_fact', key='order_item_id',
                columns=[dict(name=n, type=t, nullable=False) for n, t in TYPES.items()],
                constraints=['PRIMARY KEY (order_item_id)'])


def validate_source(pg):
    checks = read_queries(ROOT/'sql/warehouse/quality/legacy_item.sql')
    orders = pg.execute(checks['source_orders']).fetchone()
    items = pg.execute(checks['source_items']).fetchone()
    if orders != (90000, 90000) or items != (215000, 215000):
        raise ValueError('Source row count, NULL or duplicate key mismatch')
    if pg.execute(checks['source_invalid']).fetchone()[0]:
        raise ValueError('Unmatched order or invalid required source field; no source changes made')


def validate_target(pg, projection):
    report = validate_relation(pg, legacy_model(), projection)
    checks = read_queries(ROOT/'sql/warehouse/quality/legacy_item.sql')
    _, _, _, amount = pg.execute(checks['item_summary']).fetchone()
    standard = read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')['item_net_sales_amount']
    ground_truth = pg.execute(standard, window_parameters('2023-01-01', '2026-01-01', '2026-01-01')).fetchone()[0]
    source_amount = pg.execute(checks['source_amount']).fetchone()[0]
    if report['rows'] != 215000 or not amount == source_amount == ground_truth == Decimal('9024059.76'):
        raise ValueError('DWD count/amount differs from accepted first-fact baseline')
    cancelled = pg.execute(checks['cancelled_summary']).fetchone()
    report.update(net_item_amount=str(amount), source_amount=str(source_amount),
                  standard_sql_amount=str(ground_truth), baseline_amount='9024059.76',
                  cancelled_item_rows=cancelled[0], cancelled_net_amount=str(cancelled[1]), columns=TYPES)
    return report


def build_order_item_fact(pg):
    def operation():
        validate_source(pg)
        queries = read_queries(MODEL)
        result = materialize_relation(pg, legacy_model(), queries['projection'], queries['create_table'])
        report = validate_target(pg, queries['projection'])
        report['action'] = result['action']
        return report
    return build_transaction(pg, operation)
