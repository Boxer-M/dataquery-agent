"""Synthetic in-memory SQL fixtures, separate from real ODS Ground Truth."""
import json
import copy
import re
import unittest
from decimal import Decimal

import duckdb
import sqlglot
from dataquery_agent.metrics import ROOT, read_queries, window_parameters
from scripts.validate_ecommerce_metrics import validate_profile


class MetricSqlTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.db.execute('CREATE SCHEMA ods')
        self.db.execute('CREATE TABLE ods.orders(order_id INTEGER, ordered_at TIMESTAMP, status VARCHAR)')
        self.db.execute("INSERT INTO ods.orders VALUES (1,'2025-01-01','delivered'),(2,'2025-01-31 23:59:59','cancelled'),(3,'2025-02-01','processing'),(4,'2024-12-31','delivered')")
        self.db.execute('CREATE TABLE ods.order_items(order_item_id INTEGER,order_id INTEGER,quantity INTEGER,unit_price DECIMAL(12,2),discount_amount DECIMAL(12,2))')
        self.db.execute('INSERT INTO ods.order_items VALUES (1,1,2,20,5),(2,1,1,10,0),(3,2,3,5,2),(4,3,1,99,0),(5,4,1,7,0)')
        self.db.execute('CREATE TABLE ods.payments(payment_id INTEGER,order_id INTEGER,paid_at TIMESTAMP,status VARCHAR,amount DECIMAL(12,2))')
        self.db.execute("INSERT INTO ods.payments VALUES (1,1,'2025-01-01','failed',45),(2,1,'2025-01-02','captured',45),(3,2,'2025-01-31','failed',13),(4,2,'2025-02-01','captured',13),(5,4,'2025-01-01','captured',7)")
        self.db.execute('CREATE TABLE ods.returns(return_id INTEGER,order_item_id INTEGER,returned_at TIMESTAMP,refund_amount DECIMAL(12,2))')
        self.db.execute("INSERT INTO ods.returns VALUES (1,1,'2025-02-01',5),(2,1,'2025-02-02',3),(3,3,'2025-01-15',30),(4,4,'2025-01-20',1),(5,2,'2025-03-01',10)")
        self.queries = read_queries(ROOT/'sql/metrics/ecommerce_v1.sql')

    def tearDown(self):
        self.db.close()

    def evaluate(self, start='2025-01-01', end='2025-02-01', as_of='2025-03-01'):
        params = window_parameters(start, end, as_of)
        output = {}
        for key, statement in self.queries.items():
            names = re.findall(r'%\((\w+)\)s', statement)
            query = re.sub(r'%\(\w+\)s', '?', statement)
            output[key] = self.db.execute(query, [params[name] for name in names]).fetchone()
        return output

    def test_all_metrics_separate_event_times_and_grains(self):
        actual = self.evaluate()
        expected = dict(order_count=2, item_net_sales_amount=Decimal('58'), payment_attempt_count=4,
                        captured_payment_amount=Decimal('52'), payment_attempt_success_rate=0.5,
                        return_record_count=2, return_record_amount=Decimal('31'), item_return_rate=0.66666667)
        for key, value in expected.items():
            with self.subTest(metric=key):
                self.assertEqual(actual[key][0], value)
        self.assertEqual(actual['payment_attempt_success_rate'][1:], (2, 4))
        self.assertEqual(actual['item_return_rate'][1:], (2, 3))

    def test_cohort_cutoff_is_exclusive_and_duplicate_returns_do_not_inflate(self):
        early = self.evaluate(as_of='2025-02-01')['item_return_rate']
        mature = self.evaluate()['item_return_rate']
        later = self.evaluate(as_of='2025-03-02')['item_return_rate']
        self.assertEqual(early[1:], (1, 3))  # Includes known pre-order source anomaly.
        self.assertEqual(mature[1:], (2, 3))  # Two return records on item 1 count once.
        self.assertEqual(later[1:], (3, 3))

    def test_empty_window_has_zero_totals_and_undefined_rates(self):
        actual = self.evaluate('2030-01-01', '2030-02-01', '2030-02-01')
        for key, row in actual.items():
            self.assertEqual(row[0], None if key.endswith('rate') else 0)

    def test_assets_have_eight_complete_definitions_and_read_only_sql(self):
        spec = json.loads((ROOT/'metrics/ecommerce_v1.json').read_text())
        self.assertEqual(len(spec['metrics']), 8)
        self.assertEqual({m['metric_id'] for m in spec['metrics']}, set(self.queries))
        required = {'metric_id','name_zh','definition','source_fields','grain','formula','event_time',
                    'status_filter','deduplication','null_and_anomalies','availability','limitations','standard_sql'}
        for metric in spec['metrics']:
            self.assertTrue(required <= metric.keys())
        statements = list(self.queries.values()) + list(read_queries(ROOT/'sql/metrics/ecommerce_profile.sql').values())
        for statement in statements:
            parsed = sqlglot.parse(re.sub(r'%\(\w+\)s', "TIMESTAMP '2025-01-01'", statement), read='postgres')
            self.assertEqual(len(parsed), 1)
            self.assertIsInstance(parsed[0], sqlglot.exp.Query)
            self.assertFalse(any(parsed[0].find_all(sqlglot.exp.Insert, sqlglot.exp.Update,
                                                  sqlglot.exp.Delete, sqlglot.exp.Create, sqlglot.exp.Into)))

    def test_rejects_invalid_window_or_implicit_timezone_conversion(self):
        for args in [('2025-02-01','2025-01-01','2025-03-01'),
                     ('2025-01-01','2025-02-01','2025-01-15'),
                     ('2025-01-01+00:00','2025-01-01+00:00','2025-03-01'),
                     ('2025-01-01T00:00:00+08:00','2025-02-01','2025-03-01')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                window_parameters(*args)

    def test_ground_truth_rejects_quality_drift(self):
        baseline = json.loads((ROOT/'reports/ecommerce_metrics_v1.json').read_text())['profile']
        spec = json.loads((ROOT/'metrics/ecommerce_v1.json').read_text())
        validate_profile(baseline, spec)
        for section, field, value in [('keys','invalid_keys',1), ('orphans','orphans',1),
                                      ('money','nulls',1), ('event_times','nonfinite',1),
                                      ('payment_statuses','status','unreviewed_status')]:
            profile = copy.deepcopy(baseline)
            profile[section][0][field] = value
            with self.subTest(section=section), self.assertRaises(ValueError):
                validate_profile(profile, spec)
