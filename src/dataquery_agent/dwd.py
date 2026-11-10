"""Build only the SQL Shed order-item fact, preserving the source snapshot."""
from decimal import Decimal

from .metrics import ROOT, read_queries, window_parameters

MODEL = ROOT/'sql/dwd/order_item_fact.sql'
TYPES = dict(order_item_id='integer', order_id='integer', product_id='integer',
             quantity='integer', unit_price='numeric(12,2)', discount_amount='numeric(12,2)',
             net_item_amount='numeric', order_purchase_time='timestamp without time zone',
             order_status='character varying', dq_nonpositive_quantity='boolean',
             dq_negative_amount='boolean', dq_discount_exceeds_gross='boolean',
             dq_unknown_order_status='boolean', dq_invalid_order_time='boolean')


def validate_source(pg):
    orders = pg.execute('SELECT count(*),count(DISTINCT order_id) FROM ods.orders').fetchone()
    items = pg.execute('SELECT count(*),count(DISTINCT order_item_id) FROM ods.order_items').fetchone()
    if orders != (90000, 90000) or items != (215000, 215000):
        raise ValueError('Source row count, NULL or duplicate key mismatch')
    invalid = pg.execute('''SELECT count(*) FROM ods.order_items i
        LEFT JOIN ods.orders o ON o.order_id=i.order_id
        WHERE o.order_id IS NULL OR i.order_id IS NULL OR i.product_id IS NULL
        OR i.quantity IS NULL OR i.unit_price IS NULL OR i.discount_amount IS NULL
        OR o.ordered_at IS NULL OR o.status IS NULL
        OR i.unit_price::text IN ('NaN','Infinity','-Infinity')
        OR i.discount_amount::text IN ('NaN','Infinity','-Infinity')''').fetchone()[0]
    if invalid:
        raise ValueError('Unmatched order or invalid required source field; no source changes made')


def validate_target(pg, projection):
    columns = pg.execute('''SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull
        FROM pg_attribute a WHERE a.attrelid='dwd.order_item_fact'::regclass
        AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''').fetchall()
    if columns != [(name, kind, True) for name, kind in TYPES.items()]:
        raise ValueError('Existing target schema mismatch; refusing changes')
    primary = pg.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid='dwd.order_item_fact'::regclass AND contype='p'""").fetchall()
    if primary != [('PRIMARY KEY (order_item_id)',)]:
        raise ValueError('Target primary key mismatch')
    # EXCEPT ALL in both directions detects changed fields, missing rows and duplicate rows.
    differences = pg.execute(f'''WITH expected AS ({projection})
        SELECT count(*) FROM (
          (SELECT * FROM expected EXCEPT ALL SELECT * FROM dwd.order_item_fact)
          UNION ALL
          (SELECT * FROM dwd.order_item_fact EXCEPT ALL SELECT * FROM expected)
        ) differences''').fetchone()[0]
    if differences:
        raise ValueError(f'Target/source full-row mismatch ({differences}); refusing overwrite')
    rows, unique_ids, null_ids, amount = pg.execute('''SELECT count(*),count(DISTINCT order_item_id),
        count(*) FILTER(WHERE order_item_id IS NULL),sum(net_item_amount)
        FROM dwd.order_item_fact''').fetchone()
    standard = read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')['item_net_sales_amount']
    params = window_parameters('2023-01-01', '2026-01-01', '2026-01-01')
    ground_truth = pg.execute(standard, params).fetchone()[0]
    source_amount = pg.execute('SELECT sum(quantity*unit_price-discount_amount) FROM ods.order_items').fetchone()[0]
    if (rows, unique_ids, null_ids) != (215000, 215000, 0):
        raise ValueError('DWD row count or key mismatch')
    if not amount == source_amount == ground_truth == Decimal('9024059.76'):
        raise ValueError('DWD amount differs from source, standard SQL or accepted baseline')
    cancelled = pg.execute("""SELECT count(*),sum(net_item_amount) FROM dwd.order_item_fact
        WHERE order_status='cancelled'""").fetchone()
    flags = {name: pg.execute(f'SELECT count(*) FROM dwd.order_item_fact WHERE {name}').fetchone()[0]
             for name in TYPES if name.startswith('dq_')}
    return dict(rows=rows, unique_keys=unique_ids, null_keys=null_ids, full_row_differences=differences,
                net_item_amount=str(amount), source_amount=str(source_amount),
                standard_sql_amount=str(ground_truth), baseline_amount='9024059.76',
                cancelled_item_rows=cancelled[0], cancelled_net_amount=str(cancelled[1]), quality_flags=flags,
                columns={name: kind for name, kind, _ in columns})


def build_order_item_fact(pg):
    """Autocommit connection required; the schema, rows and verification share one transaction."""
    if not pg.autocommit:
        raise ValueError('Use an autocommit connection for an independent build transaction')
    # Session lock is acquired before the snapshot so concurrent builders see committed predecessors.
    if not pg.execute('SELECT pg_try_advisory_lock(426025)').fetchone()[0]:
        raise ValueError('Another DWD build is active; no changes made')
    try:
        with pg.transaction():
            pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            pg.execute("SET LOCAL statement_timeout = '30s'")
            validate_source(pg)
            queries = read_queries(MODEL)
            projection = queries['projection'].rstrip(';')
            existing = pg.execute("SELECT to_regclass('dwd.order_item_fact')").fetchone()[0]
            if not existing:
                pg.execute('CREATE SCHEMA IF NOT EXISTS dwd')
                pg.execute(queries['create_table'])
                pg.execute('INSERT INTO dwd.order_item_fact '+projection)
            report = validate_target(pg, projection)
            report['action'] = 'verified_existing' if existing else 'created'
        return report
    finally:
        pg.execute('SELECT pg_advisory_unlock(426025)')
