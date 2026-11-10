"""DWS contracts plus isolated PostgreSQL integration, never writes personal source data."""
import copy
import json
import os
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from psycopg import sql
from dataquery_agent.metrics import ROOT,read_queries
from dataquery_agent.dws import models,parameters,build_dws,all_inputs,compare_metric,numeric_equal
from dataquery_agent.dwd import materialize_relation
from dataquery_agent.warehouse import build_batch_a
import tests.test_warehouse_postgres as fixtures


class DwsContractTests(unittest.TestCase):
    def test_exact_numeric_baseline_preserves_null_and_integer_semantics(self):
        self.assertTrue(numeric_equal(Decimal('90000'),90000))
        self.assertTrue(numeric_equal(Decimal('9024059.76'),'9024059.76'))
        self.assertFalse(numeric_equal(Decimal('9024059.77'),'9024059.76'))
        self.assertFalse(numeric_equal(None,0))
        self.assertTrue(numeric_equal(None,None))

    def test_six_composite_grains_and_no_ads(self):
        manifest=models()
        self.assertEqual(len(manifest['models']),6)
        for model in manifest['models']:
            self.assertTrue(model['name'].startswith('dws.'))
            self.assertGreaterEqual(len(model['keys']),3)
            self.assertTrue(set(model['keys']) <= {c['name'] for c in model['columns']})
            self.assertFalse(any(c['name'].endswith('_rate') for c in model['columns']))

    def test_partial_day_and_implicit_timezone_are_rejected(self):
        for start in ['2025-01-01T01:00:00','2025-01-01T00:00:00+08:00']:
            with self.assertRaises(ValueError):parameters(start,'2025-02-01','2026-01-01')

    def test_supported_registry_is_8_plus_18_and_deferred_are_not_in_sql(self):
        queries=read_queries(ROOT/'sql/warehouse/dws_metrics/dws.sql')
        registry=json.loads((ROOT/'warehouse/dws_metrics.json').read_text())['metrics']
        original=read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')
        self.assertEqual(len(queries),26)
        self.assertEqual(set(queries),{m['metric_id'] for m in registry})
        self.assertTrue(set(original)<=set(queries))
        self.assertNotIn('payment_item_amount_mismatch_order_count',queries)


@unittest.skipUnless(os.environ.get('WAREHOUSE_POSTGRES_TESTS')=='1','Set WAREHOUSE_POSTGRES_TESTS=1 for isolated DWS tests')
class DwsPostgresTests(unittest.TestCase):
    def setUp(self):
        fixtures.WarehousePostgresTests.setUp(self)

    def cleanup_database(self):
        fixtures.WarehousePostgresTests.cleanup_database(self)

    def prepare(self):
        build_batch_a(self.pg,baseline_path=None)
        self.before=all_inputs(self.pg)

    def build(self):
        return build_dws(self.pg,baseline_path=None)

    def test_complete_models_independent_metrics_and_source_preservation(self):
        self.prepare();result=self.build()
        self.assertEqual({n:r['rows'] for n,r in result['models'].items()},dict(
            [('dws.order_day',4),('dws.product_sales_day',5),('dws.payment_day',6),
             ('dws.return_day',3),('dws.item_return_cohort_day',5),('dws.customer_order_day',4)]))
        metrics=result['metrics']
        value=lambda mid,window='full':metrics[mid]['windows'][window][0]['value']
        self.assertEqual(value('item_net_sales_amount'),Decimal('23.00'))
        self.assertEqual(value('captured_payment_amount'),Decimal('26.00'))
        self.assertEqual(value('return_record_amount'),Decimal('38.00'))
        self.assertEqual(value('customer_repeat_order_rate'),Decimal('0.33333333'))
        self.assertEqual(value('customer_repeat_order_rate','january_2025'),Decimal('0.50000000'))
        self.assertEqual(value('return_record_count','january_2025'),0)
        self.assertEqual(value('item_return_rate','january_2025'),Decimal('0.66666667'))
        for mid in ['item_return_rate','payment_attempt_success_rate','customer_repeat_order_rate','average_order_record_amount']:
            self.assertIsNone(value(mid,'empty'))
        self.assertEqual(result['totals']['anomalous_return_items'],2)
        self.assertEqual(len(result['available_extensions']),18)
        self.assertEqual(self.before,all_inputs(self.pg))
        queries=read_queries(ROOT/'sql/warehouse/dws_metrics/dws.sql')
        reference=read_queries(ROOT/'sql/warehouse/dws_metrics/ods.sql')
        params=parameters('2025-01-31','2025-02-01','2026-01-01')
        _,groups=compare_metric(self.pg,reference['captured_payment_amount'],queries['captured_payment_amount'],params,'channel')
        self.assertEqual(groups,[])  # A failed-only channel must not invent a captured group.

    def test_normal_repeat_and_readonly_repeat_do_not_append(self):
        self.prepare();first=self.build();second=self.build()
        for n in first['models']:
            self.assertEqual(second['models'][n]['action'],'verified_existing')
            self.assertEqual(first['models'][n]['rows'],second['models'][n]['rows'])
        checked=build_dws(self.pg,baseline_path=None,check_only=True)
        self.assertEqual(checked['metrics'],first['metrics'])
        self.assertEqual(self.before,all_inputs(self.pg))

    def test_whole_dws_batch_rolls_back_after_new_insert(self):
        self.prepare()
        def injected(pg,model,query,ddl):
            result=materialize_relation(pg,model,query,ddl)
            if model['name']=='dws.return_day':raise ValueError('injected after DWS insert')
            return result
        with patch('dataquery_agent.dws.materialize_relation',side_effect=injected):
            with self.assertRaisesRegex(ValueError,'injected'):self.build()
        for model in models()['models']:
            self.assertIsNone(self.pg.execute('SELECT to_regclass(%s)',(model['name'],)).fetchone()[0])
        self.assertEqual(self.before,all_inputs(self.pg))

    def test_existing_schema_or_content_is_never_repaired(self):
        self.prepare();self.build()
        self.pg.execute('UPDATE dws.order_day SET order_count=order_count+1')
        with self.assertRaisesRegex(ValueError,'full-row mismatch'):self.build()
        self.assertEqual(self.pg.execute('SELECT sum(order_count) FROM dws.order_day').fetchone()[0],8)
        self.assertEqual(self.before,all_inputs(self.pg))

    def test_cutoff_is_exclusive_and_multiple_return_records_count_once(self):
        self.pg.execute("INSERT INTO ods.returns VALUES (205,12,'2026-01-01','fixture cutoff',2,false)")
        self.prepare();result=self.build()
        self.assertEqual(result['totals']['returned_items'],3)
        self.assertEqual(result['totals']['cohort_items'],6)
        self.assertEqual(result['totals']['returns'],5)
        changed=copy.deepcopy(models());changed['as_of']='2026-02-01T00:00:00'
        with patch('dataquery_agent.dws.models',return_value=changed):
            with self.assertRaisesRegex(ValueError,'full-row mismatch'):self.build()
        self.assertEqual(self.before,all_inputs(self.pg))

    def test_sources_have_write_guards_and_zero_denominator_share_is_null(self):
        self.prepare()
        self.pg.execute("CREATE FUNCTION public.dws_input_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'input write'; END $$")
        for name in self.before:
            self.pg.execute(sql.SQL('CREATE TRIGGER dws_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON {} FOR EACH STATEMENT EXECUTE FUNCTION public.dws_input_guard()').format(sql.Identifier(*name.split('.'))))
        self.build()
        self.pg.execute('UPDATE dws.product_sales_day SET item_net_sales_amount=0')
        query=read_queries(ROOT/'sql/warehouse/dws_metrics/dws.sql')['category_sales_share']
        params=parameters('2023-01-01','2026-01-01','2026-01-01')
        _,rows=compare_metric(self.pg,query,query,params)
        self.assertTrue(rows)
        self.assertTrue(all(r['value'] is None for r in rows))
        self.assertEqual(self.before,all_inputs(self.pg))

    def test_missing_dependency_stops_without_new_dws(self):
        self.prepare()
        self.pg.execute('ALTER TABLE dwd.payment_attempt_fact RENAME TO preserved_payment_fixture')
        with self.assertRaisesRegex(ValueError,'Missing A dependency'):self.build()
        self.assertIsNone(self.pg.execute("SELECT to_regclass('dws.order_day')").fetchone()[0])
