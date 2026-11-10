"""Opt-in integration tests: only randomly named, newly created test databases are cleaned."""
import os
import re
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from dataquery_agent.metrics import ROOT
from dataquery_agent.warehouse import build_batch_a, load_models, source_fingerprints
from dataquery_agent.dwd import materialize_relation


@unittest.skipUnless(os.environ.get('WAREHOUSE_POSTGRES_TESTS')=='1', 'Set WAREHOUSE_POSTGRES_TESTS=1 for isolated PostgreSQL tests')
class WarehousePostgresTests(unittest.TestCase):
    def setUp(self):
        env = dotenv_values(ROOT/'.env')
        self.config = dict(host='127.0.0.1',port=5433,user=env['POSTGRES_USER'],password=env['POSTGRES_PASSWORD'])
        self.name = 'dataquery_test_a_'+uuid.uuid4().hex
        with psycopg.connect(**self.config,dbname='postgres',autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)))
        self.addCleanup(self.cleanup_database)
        self.pg = psycopg.connect(**self.config,dbname=self.name,autocommit=True)
        ddl = (ROOT/'sql/load_sqlshed_ods.sql').read_text().split('COPY categories')[0]
        with self.pg.transaction():
            self.pg.execute(ddl)
            self.pg.execute((ROOT/'tests/fixtures/sqlshed_batch_a.sql').read_text())
            self.pg.execute('CREATE SCHEMA dwd')
            self.pg.execute((ROOT/'sql/warehouse/ddl/dwd/order_item_fact.sql').read_text())
            self.pg.execute('INSERT INTO dwd.order_item_fact '+(ROOT/'sql/warehouse/transform/dwd/order_item_fact.sql').read_text())

    def cleanup_database(self):
        if hasattr(self,'pg'):
            self.pg.close()
        if not re.fullmatch(r'dataquery_test_a_[0-9a-f]{32}',self.name):
            raise RuntimeError('Refusing cleanup outside generated test namespace')
        with psycopg.connect(**self.config,dbname='postgres',autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(self.name)))

    def build(self):
        return build_batch_a(self.pg,baseline_path=None)

    def test_complete_batch_grains_money_anomalies_dates_and_repeat(self):
        first = self.build()
        self.assertEqual({k:v['rows'] for k,v in first['models'].items()},
            {'dim.customer':3,'dim.category':2,'dim.channel':2,'dim.date':1496,'dim.product':2,
             'dwd.order_fact':4,'dwd.order_item_fact':6,'dwd.payment_attempt_fact':6,'dwd.return_record_fact':4})
        self.assertEqual(first['metrics']['full']['results']['item_net_sales_amount']['value'],Decimal('23.00'))
        self.assertEqual(first['metrics']['full']['results']['captured_payment_amount']['value'],Decimal('26.00'))
        self.assertEqual(first['models']['dwd.return_record_fact']['quality_flags'],
            {'dq_return_before_order':1,'dq_refund_above_item_net':2,'dq_negative_amount':1})
        self.assertEqual(self.pg.execute('SELECT iso_week_year,iso_week,iso_weekday FROM dim.date WHERE date_value=%s',('2021-01-01',)).fetchone(),(2020,53,5))
        self.assertEqual(self.pg.execute("SELECT count(*) FROM dim.date WHERE date_value='2024-02-29'").fetchone()[0],1)
        self.assertEqual(first['models']['dwd.order_item_fact']['action'],'verified_existing')
        repeated = self.build()
        for model in repeated['models'].values():
            self.assertEqual(model['action'],'verified_existing')
        self.assertEqual(first['source_fingerprints'],repeated['source_fingerprints'])
        self.assertEqual(first['existing_item_fingerprint'],repeated['existing_item_fingerprint'])

    def test_failure_after_new_fact_insert_rolls_back_entire_batch(self):
        before = source_fingerprints(self.pg)
        def fail_after_payment(pg, model, projection, ddl):
            result = materialize_relation(pg,model,projection,ddl)
            if model['name']=='dwd.payment_attempt_fact':
                raise ValueError('injected after insert')
            return result
        with patch('dataquery_agent.warehouse.materialize_relation',side_effect=fail_after_payment):
            with self.assertRaisesRegex(ValueError,'injected'):
                self.build()
        for model in load_models()['models']:
            exists = self.pg.execute('SELECT to_regclass(%s)',(model['name'],)).fetchone()[0]
            self.assertEqual(exists is not None,model['reuse_only'])
        self.assertEqual(before,source_fingerprints(self.pg))
        self.assertEqual(self.pg.execute('SELECT count(*) FROM dwd.order_item_fact').fetchone()[0],6)

    def test_existing_target_schema_conflict_is_not_overwritten(self):
        self.pg.execute('CREATE SCHEMA dim')
        self.pg.execute('CREATE TABLE dim.customer(wrong integer)')
        with self.assertRaisesRegex(ValueError,'schema mismatch'):
            self.build()
        self.assertEqual(self.pg.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='dim' AND table_name='customer'").fetchall(),[('wrong',)])
        self.assertIsNone(self.pg.execute("SELECT to_regclass('dim.category')").fetchone()[0])

    def test_existing_content_conflict_is_rejected_without_repair(self):
        self.build()
        self.pg.execute("UPDATE dim.customer SET city='changed in isolated test' WHERE customer_id=1")
        with self.assertRaisesRegex(ValueError,'full-row mismatch'):
            self.build()
        self.assertEqual(self.pg.execute('SELECT city FROM dim.customer WHERE customer_id=1').fetchone()[0],'changed in isolated test')

    def test_category_cycle_is_blocked_before_any_new_model(self):
        self.pg.execute('UPDATE ods.categories SET parent_category_id=2 WHERE category_id=1')
        with self.assertRaisesRegex(ValueError,'hierarchy cycle'):
            self.build()
        self.assertIsNone(self.pg.execute("SELECT to_regclass('dim.customer')").fetchone()[0])

    def test_orphan_source_is_blocked_and_not_silently_dropped(self):
        self.pg.execute('ALTER TABLE ods.order_items DROP CONSTRAINT order_items_order_id_fkey')
        self.pg.execute('INSERT INTO ods.order_items VALUES (999,999,1,1,1,0)')
        with self.assertRaisesRegex(ValueError,'orphan'):
            self.build()
        self.assertEqual(self.pg.execute('SELECT count(*) FROM ods.order_items WHERE order_id=999').fetchone()[0],1)

    def test_existing_item_has_write_guard_and_batch_never_writes_it(self):
        self.pg.execute("""CREATE FUNCTION public.forbid_fixture_item_write() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'existing item write forbidden'; END $$;
            CREATE TRIGGER fixture_guard BEFORE INSERT OR UPDATE OR DELETE ON dwd.order_item_fact
            FOR EACH STATEMENT EXECUTE FUNCTION public.forbid_fixture_item_write();""")
        self.build()
        checked = build_batch_a(self.pg,baseline_path=None,check_only=True)
        self.assertTrue(checked['source_and_existing_item_unchanged'])

    def test_lock_and_missing_dependency_protect_existing_data(self):
        with psycopg.connect(**self.config,dbname=self.name,autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(426025)')
            with self.assertRaisesRegex(ValueError,'Another DWD build'):
                self.build()
        self.pg.execute('ALTER TABLE dwd.order_item_fact RENAME TO preserved_fixture_item')
        with self.assertRaisesRegex(ValueError,'Required existing'):
            self.build()
        self.assertEqual(self.pg.execute('SELECT count(*) FROM dwd.preserved_fixture_item').fetchone()[0],6)
