"""Validate SQL Shed Parquet and atomically load an isolated PostgreSQL ODS."""
import hashlib
import json
from pathlib import Path

import duckdb
import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / 'data/raw/sqlshed'
COUNTS = dict(categories=24, customers=12000, products=2000, orders=90000,
              order_items=215000, payments=95000, returns=12000)
KEYS = dict(categories='category_id', customers='customer_id', products='product_id',
            orders='order_id', order_items='order_item_id', payments='payment_id', returns='return_id')
RELATIONS = [('categories', 'parent_category_id', 'categories', 'category_id'),
             ('products', 'category_id', 'categories', 'category_id'),
             ('orders', 'customer_id', 'customers', 'customer_id'),
             ('order_items', 'order_id', 'orders', 'order_id'),
             ('order_items', 'product_id', 'products', 'product_id'),
             ('payments', 'order_id', 'orders', 'order_id'),
             ('returns', 'order_item_id', 'order_items', 'order_item_id')]


def source_connection(raw=RAW):
    manifest = json.loads((raw / 'download_manifest.json').read_text())
    for name, record in manifest['files'].items():
        payload = (raw / name).read_bytes()
        if len(payload) != record['size_bytes'] or hashlib.sha256(payload).hexdigest() != record['sha256']:
            raise RuntimeError(f'Source checksum mismatch: {name}')
    required = {f'{table}.parquet' for table in COUNTS} | {'ecommerce_orders.schema.sql'}
    if set(manifest['files']) != required:
        raise RuntimeError('Incomplete source manifest')
    con = duckdb.connect()
    for table in COUNTS:
        con.read_parquet(str(raw / f"{table}.parquet")).create_view(table)
    return con


def audit(con):
    report = {'source': 'SQL Shed CC0 synthetic practice data', 'tables': {}, 'orphans': {}}
    for table, expected in COUNTS.items():
        count, distinct, nulls = con.execute(
            f'SELECT count(*), count(DISTINCT {KEYS[table]}), count(*) FILTER (WHERE {KEYS[table]} IS NULL) FROM {table}'
        ).fetchone()
        if count != expected or distinct != count or nulls:
            raise RuntimeError(f'Row count or primary key failure: {table}')
        fields = con.execute(f'DESCRIBE {table}').fetchall()
        details = {}
        for name, kind, *_ in fields:
            missing = con.execute(f'SELECT count(*) FROM {table} WHERE {name} IS NULL').fetchone()[0]
            detail = {'type': kind, 'nulls': missing}
            if kind.startswith('DECIMAL'):
                if kind != 'DECIMAL(12,2)':
                    raise RuntimeError(f'Unexpected money precision: {table}.{name}')
                low, high, negative = con.execute(f'SELECT min({name}),max({name}),count(*) FILTER (WHERE {name}<0) FROM {table}').fetchone()
                detail.update(min=str(low), max=str(high), negative=negative)
            if kind in ('DATE', 'TIMESTAMP'):
                low, high, invalid = con.execute(f'SELECT min({name}),max({name}),count(*) FILTER (WHERE NOT isfinite({name})) FROM {table}').fetchone()
                if invalid:
                    raise RuntimeError(f'Nonfinite date: {table}.{name}')
                detail.update(min=str(low), max=str(high), nonfinite=invalid)
            details[name] = detail
        report['tables'][table] = {'rows': count, 'pk_nulls': nulls, 'pk_duplicates': count-distinct, 'columns': details}
    for child, fk, parent, pk in RELATIONS:
        count = con.execute(f'SELECT count(*) FROM {child} c LEFT JOIN {parent} p ON c.{fk}=p.{pk} WHERE c.{fk} IS NOT NULL AND p.{pk} IS NULL').fetchone()[0]
        report['orphans'][f'{child}.{fk}'] = count
        if count:
            raise RuntimeError(f'Orphan keys: {child}.{fk}')
    report['statuses'] = {t: dict(con.execute(f'SELECT status,count(*) FROM {t} GROUP BY status ORDER BY status').fetchall()) for t in ('orders', 'payments')}
    checks = {
        'shipping_before_order': 'SELECT count(*) FROM orders WHERE shipped_at<ordered_at',
        'payment_before_order': 'SELECT count(*) FROM payments p JOIN orders o USING(order_id) WHERE p.paid_at<o.ordered_at',
        'return_before_order': 'SELECT count(*) FROM returns r JOIN order_items i USING(order_item_id) JOIN orders o USING(order_id) WHERE r.returned_at<o.ordered_at',
        'refund_above_item_net': 'SELECT count(*) FROM returns r JOIN order_items i USING(order_item_id) WHERE refund_amount>quantity*unit_price-discount_amount',
        'nonpositive_quantity': 'SELECT count(*) FROM order_items WHERE quantity<=0',
        'discount_above_item_gross': 'SELECT count(*) FROM order_items WHERE discount_amount>quantity*unit_price',
        'multiple_returns_per_item': 'SELECT count(*) FROM (SELECT order_item_id FROM returns GROUP BY 1 HAVING count(*)>1)',
    }
    report['business_checks'] = {name: con.execute(query).fetchone()[0] for name, query in checks.items()}
    report['payment_totals_by_status'] = {status: str(amount) for status, amount in con.execute('SELECT status,sum(amount) FROM payments GROUP BY status').fetchall()}
    count, difference = con.execute('''WITH items AS (
        SELECT order_id,sum(quantity*unit_price-discount_amount) net FROM order_items GROUP BY 1
    ), paid AS (
        SELECT order_id,sum(amount) FILTER (WHERE status='captured') captured FROM payments GROUP BY 1
    ) SELECT count(*) FILTER (WHERE coalesce(net,0)<>coalesce(captured,0)),
        sum(coalesce(captured,0)-coalesce(net,0))
        FROM orders LEFT JOIN items USING(order_id) LEFT JOIN paid USING(order_id)''').fetchone()
    report['payment_vs_item_net'] = {'different_orders': count, 'captured_minus_item_net': str(difference)}
    return report


def verify_target(pg, source):
    for table in COUNTS:
        cursor = source.execute(f'SELECT * FROM {table} ORDER BY {KEYS[table]}')
        # Separate DuckDB cursor prevents the next statement from invalidating batches.
        with pg.cursor() as target:
            target.execute(sql.SQL('SELECT * FROM ods.{} ORDER BY {}').format(sql.Identifier(table), sql.Identifier(KEYS[table])))
            while True:
                original = cursor.fetchmany(2000)
                actual = target.fetchmany(2000)
                if original != actual:
                    raise RuntimeError(f'Target content mismatch: {table}')
                if not original:
                    break
        columns = pg.execute("SELECT data_type,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema='ods' AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall()
        fields = source.execute(f'DESCRIBE {table}').fetchall()
        if len(columns) != len(fields):
            raise RuntimeError(f'Target column mismatch: {table}')
        for (_, kind, *_), (dtype, precision, scale) in zip(fields, columns):
            expected = {'INTEGER':'integer','VARCHAR':'character varying','DATE':'date','TIMESTAMP':'timestamp without time zone','BOOLEAN':'boolean'}.get(kind, 'numeric')
            if dtype != expected or (kind.startswith('DECIMAL') and (precision, scale) != (12, 2)):
                raise RuntimeError(f'Target type mismatch: {table}')


def load(pg, source):
    with pg.transaction():
        pg.execute('SELECT pg_advisory_xact_lock(426024)')
        exists = pg.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='ods')").fetchone()[0]
        if exists:
            verify_target(pg, source)
            return 'verified_existing'
        if pg.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()[0]:
            raise RuntimeError('Existing business tables: refusing import')
        pg.execute((ROOT / 'sql/load_sqlshed_ods.sql').read_text())
        for table in COUNTS:
            reader = source.execute(f'SELECT * FROM {table}')
            with pg.cursor().copy(sql.SQL('COPY ods.{} FROM STDIN').format(sql.Identifier(table))) as copy:
                while rows := reader.fetchmany(2000):
                    for row in rows:
                        copy.write_row(row)
        verify_target(pg, source)
    return 'imported'


def main():
    source = source_connection()
    report = audit(source)
    env = dotenv_values(ROOT / '.env')
    config = dict(host='127.0.0.1', port=5433, user=env['POSTGRES_USER'], password=env['POSTGRES_PASSWORD'])
    with psycopg.connect(**config, dbname='postgres', autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname='sqlshed'").fetchone():
            admin.execute('CREATE DATABASE sqlshed')
    with psycopg.connect(**config, dbname='sqlshed', autocommit=True) as pg:
        report['ingestion'] = load(pg, source)
    with psycopg.connect(**config, dbname='olist') as pg:
        report['olist_orders'] = pg.execute('SELECT count(*) FROM ods.orders').fetchone()[0]
        if report['olist_orders'] != 99441:
            raise RuntimeError('Unexpected Olist order count')
    (ROOT / 'reports/sqlshed_ingestion.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'ingestion': report['ingestion'], 'rows': COUNTS, 'olist_orders': report['olist_orders']}))


if __name__ == '__main__':
    main()
