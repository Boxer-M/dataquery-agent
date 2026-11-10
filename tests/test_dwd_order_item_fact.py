"""Synthetic in-memory projection fixtures; no ODS source data is modified."""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock

import duckdb
import sqlglot
from dataquery_agent.dwd import MODEL, TYPES, build_order_item_fact, validate_source
from dataquery_agent.metrics import read_queries


class OrderItemProjectionTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.db.execute('CREATE SCHEMA ods')
        self.db.execute('CREATE TABLE ods.orders(order_id INTEGER,ordered_at TIMESTAMP,status VARCHAR)')
        self.db.execute("INSERT INTO ods.orders VALUES (1,'2025-01-01','cancelled'),(2,'2025-01-02','delivered'),(3,'2025-01-03','unreviewed'),(4,'infinity','processing')")
        self.db.execute('CREATE TABLE ods.order_items(order_item_id INTEGER,order_id INTEGER,product_id INTEGER,quantity INTEGER,unit_price DECIMAL(12,2),discount_amount DECIMAL(12,2))')
        self.db.execute('INSERT INTO ods.order_items VALUES (1,1,11,2,10.10,0.20),(2,2,12,0,5,0),(3,2,13,1,-3,-1),(4,3,14,2,1,3),(5,4,15,1000000000,1000,0)')
        self.query = read_queries(MODEL)['projection']
        cursor = self.db.execute(self.query)
        self.columns = [c[0] for c in cursor.description]
        self.rows = {row[0]: dict(zip(self.columns, row)) for row in cursor.fetchall()}

    def tearDown(self):
        self.db.close()

    def test_one_row_per_item_and_cancelled_order_is_preserved(self):
        self.assertEqual(set(self.rows), {1, 2, 3, 4, 5})
        self.assertEqual(self.rows[1]['order_status'], 'cancelled')
        self.assertEqual(str(self.rows[1]['order_purchase_time']), '2025-01-01 00:00:00')
        self.assertEqual(self.columns, list(TYPES))

    def test_exact_decimal_formula_and_no_premature_precision_limit(self):
        self.assertEqual(self.rows[1]['net_item_amount'], Decimal('20.00'))
        self.assertEqual(self.rows[5]['net_item_amount'], Decimal('1000000000000.00'))
        self.assertIsInstance(self.rows[1]['net_item_amount'], Decimal)

    def test_business_anomalies_are_flagged_without_correction(self):
        self.assertTrue(self.rows[2]['dq_nonpositive_quantity'])
        self.assertTrue(self.rows[3]['dq_negative_amount'])
        self.assertEqual(self.rows[3]['unit_price'], Decimal('-3.00'))
        self.assertEqual(self.rows[3]['discount_amount'], Decimal('-1.00'))
        self.assertEqual(self.rows[3]['net_item_amount'], Decimal('-2.00'))
        self.assertTrue(self.rows[4]['dq_discount_exceeds_gross'])
        self.assertTrue(self.rows[4]['dq_unknown_order_status'])
        self.assertEqual(self.rows[4]['net_item_amount'], Decimal('-1.00'))
        self.assertTrue(self.rows[5]['dq_invalid_order_time'])
        self.assertFalse(any(self.rows[1][k] for k in TYPES if k.startswith('dq_')))

    def test_projection_only_joins_orders_and_never_filters_source_rows(self):
        tree = sqlglot.parse_one(self.query, read='postgres')
        self.assertEqual({t.name for t in tree.find_all(sqlglot.exp.Table)}, {'orders','order_items'})
        self.assertEqual(len(list(tree.find_all(sqlglot.exp.Join))), 1)
        self.assertIsNone(tree.find(sqlglot.exp.Where))

    def test_invalid_order_keys_or_item_keys_block_the_build(self):
        for counts in [[(90000,89999),(215000,215000)], [(90000,90000),(215000,214999)]]:
            pg = MagicMock()
            pg.execute.return_value.fetchone.side_effect = counts
            with self.assertRaisesRegex(ValueError, 'key mismatch'):
                validate_source(pg)

    def test_unmatched_order_or_missing_required_field_blocks_the_build(self):
        pg = MagicMock()
        pg.execute.return_value.fetchone.side_effect = [(90000,90000),(215000,215000),(1,)]
        with self.assertRaisesRegex(ValueError, 'Unmatched order'):
            validate_source(pg)

    def test_active_builder_and_nested_transaction_are_rejected(self):
        pg = MagicMock()
        pg.autocommit = True
        pg.execute.return_value.fetchone.return_value = (False,)
        with self.assertRaisesRegex(ValueError, 'Another DWD build'):
            build_order_item_fact(pg)
        pg.transaction.assert_not_called()
        pg.autocommit = False
        with self.assertRaisesRegex(ValueError, 'autocommit'):
            build_order_item_fact(pg)
