"""ADS contracts and real isolated PostgreSQL tests, never mock production facts."""
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from psycopg import sql
from dataquery_agent.metrics import ROOT
from dataquery_agent.ads import models,build_ads,create_view,inputs
from dataquery_agent.dws import build_dws
from dataquery_agent.warehouse import build_batch_a
import tests.test_warehouse_postgres as fixtures


class AdsContractTests(unittest.TestCase):
    def test_three_view_grains_and_fixed_observation(self):
        manifest=models()
        self.assertEqual(manifest['as_of'],'2026-01-01T00:00:00')
        self.assertEqual([m['keys'] for m in manifest['models']],
            [['event_date','channel'],['order_id'],['order_date','channel','product_id','as_of']])
        for model in manifest['models']:
            self.assertTrue((ROOT/model['sql']).is_file())
            self.assertNotIn('ods.',(ROOT/model['sql']).read_text())

    def test_no_metric_promotion(self):
        metrics=json.loads((ROOT/'metrics/warehouse_catalog_v1.json').read_text())['metrics']
        self.assertEqual(sum(m['definition_status']=='available' for m in metrics),18)
        self.assertEqual(sum(m['definition_status']=='defined_unverified' for m in metrics),4)
        self.assertEqual(sum(m['availability']=='unsupported' for m in metrics),8)


@unittest.skipUnless(os.environ.get('WAREHOUSE_POSTGRES_TESTS')=='1','Set WAREHOUSE_POSTGRES_TESTS=1 for isolated ADS tests')
class AdsPostgresTests(unittest.TestCase):
    def setUp(self):fixtures.WarehousePostgresTests.setUp(self)
    def cleanup_database(self):fixtures.WarehousePostgresTests.cleanup_database(self)

    def prepare(self):
        build_batch_a(self.pg,baseline_path=None)
        build_dws(self.pg,baseline_path=None)
        self.before=inputs(self.pg)

    def test_source_ground_truth_and_no_join_multiplication(self):
        self.prepare();result=build_ads(self.pg)
        self.assertEqual(result['ground_truth_comparisons'],24)
        self.assertEqual(result['models']['ads.payment_anomaly_order']['rows'],4)
        full=result['windows']['full/payment_anomaly_order']
        self.assertEqual((full['item_net'],full['attempts'],full['paid']),(Decimal('23'),6,Decimal('26')))
        overview=result['windows']['full/business_overview_day']
        self.assertEqual((overview['orders'],overview['returns'],overview['return_amount']),(4,4,Decimal('38')))
        self.assertEqual((overview['early_returns'],overview['excessive_returns']),(1,2))
        self.assertEqual(result['windows']['january_2025/product_return_cohort']['item_return_rate'],Decimal('0.66666667'))
        self.assertEqual(result['windows']['january_2025/business_overview_day']['returns'],0)
        self.assertEqual(self.before,inputs(self.pg))

    def test_repeat_readonly_and_definition_stability(self):
        self.prepare();first=build_ads(self.pg);second=build_ads(self.pg)
        checked=build_ads(self.pg,check_only=True)
        for n in first['models']:
            self.assertEqual(second['models'][n]['action'],'verified_existing')
            self.assertEqual(first['models'][n]['definition_sha256'],checked['models'][n]['definition_sha256'])
        self.assertEqual(first['windows'],checked['windows'])

    def test_transaction_rolls_back_all_new_views(self):
        self.prepare()
        def injected(pg,model,query):
            result=create_view(pg,model,query)
            if model['name']=='ads.payment_anomaly_order':raise ValueError('injected after view creation')
            return result
        with patch('dataquery_agent.ads.create_view',side_effect=injected):
            with self.assertRaisesRegex(ValueError,'injected'):build_ads(self.pg)
        for m in models()['models']:
            self.assertIsNone(self.pg.execute('SELECT to_regclass(%s)',(m['name'],)).fetchone()[0])
        self.assertEqual(self.before,inputs(self.pg))

    def test_existing_object_conflict_is_not_replaced(self):
        self.prepare();self.pg.execute('CREATE SCHEMA ads')
        self.pg.execute('CREATE TABLE ads.business_overview_day(preserved integer)')
        with self.assertRaisesRegex(ValueError,'ordinary view'):build_ads(self.pg)
        self.assertEqual(self.pg.execute("SELECT relkind FROM pg_class WHERE oid='ads.business_overview_day'::regclass").fetchone()[0],'r')
        self.assertIsNone(self.pg.execute("SELECT to_regclass('ads.payment_anomaly_order')").fetchone()[0])

    def test_altered_existing_definition_is_refused_even_when_rows_equal(self):
        self.prepare();build_ads(self.pg)
        definition=self.pg.execute("SELECT pg_get_viewdef('ads.business_overview_day'::regclass,true)").fetchone()[0]
        self.pg.execute('CREATE OR REPLACE VIEW ads.business_overview_day WITH (security_barrier=true) AS SELECT * FROM ('+definition.strip().rstrip(';')+') preserved WHERE true')
        with self.assertRaisesRegex(ValueError,'definition conflict'):build_ads(self.pg)
        self.assertEqual(self.before,inputs(self.pg))

    def test_no_attempt_zero_denominator_null_and_empty_windows(self):
        self.pg.execute('DELETE FROM ods.payments WHERE order_id=4')
        self.prepare();r=build_ads(self.pg)
        row=self.pg.execute('SELECT payment_attempt_count,first_payment_at,last_payment_at,payment_failed_attempt_rate,has_multiple_attempts FROM ads.payment_anomaly_order WHERE order_id=4').fetchone()
        self.assertEqual(row,(0,None,None,None,False))
        for view in ['payment_anomaly_order','product_return_cohort']:
            empty=r['windows']['empty/'+view]
            self.assertIsNone(empty['failed_rate' if view=='payment_anomaly_order' else 'item_return_rate'])
        self.assertEqual(self.pg.execute("SELECT count(*) FROM ads.business_overview_day WHERE event_date='2022-01-01'").fetchone()[0],0)
        self.assertEqual(self.pg.execute("SELECT order_count,payment_attempt_count,return_record_count FROM ads.business_overview_day WHERE event_date='2024-12-31'").fetchone(),(0,0,1))
        # Explicit zero-denominator mutation is confined to this disposable test DB.
        self.pg.execute('UPDATE dws.item_return_cohort_day SET cohort_item_count=0,returned_item_count=0,anomalous_return_item_count=0')
        self.assertTrue(all(row[0] is None for row in self.pg.execute('SELECT item_return_rate FROM ads.product_return_cohort')))

    def test_cutoff_exclusion_and_multiple_attempts_not_payment_orders(self):
        self.pg.execute("INSERT INTO ods.payments VALUES (107,1,'card',99,'2026-01-01','captured',NULL)")
        self.prepare();r=build_ads(self.pg)
        self.assertEqual(self.pg.execute('SELECT payment_attempt_count,captured_attempt_count,failed_attempt_count,has_multiple_attempts FROM ads.payment_anomaly_order WHERE order_id=1').fetchone(),(2,1,1,True))
        self.assertEqual(r['windows']['full/payment_anomaly_order']['multiple_attempt_orders'],2)
        self.assertEqual(r['windows']['full/business_overview_day']['attempts'],6)
        self.assertEqual(self.pg.execute("SELECT payment_attempt_count FROM ads.business_overview_day WHERE event_date='2026-01-01'").fetchone()[0],1)

    def test_all_inputs_write_guard_and_missing_dependency(self):
        self.prepare()
        self.pg.execute("CREATE FUNCTION public.ads_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'input write'; END $$")
        for name in self.before:
            self.pg.execute(sql.SQL('CREATE TRIGGER ads_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON {} FOR EACH STATEMENT EXECUTE FUNCTION public.ads_guard()').format(sql.Identifier(*name.split('.'))))
        build_ads(self.pg)
        self.assertEqual(self.before,inputs(self.pg))
        self.pg.execute('ALTER TABLE dws.return_day RENAME TO preserved_fixture_returns')
        with self.assertRaisesRegex(ValueError,'Missing ADS dependency'):build_ads(self.pg)
