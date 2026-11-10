"""Small synthetic fixtures test validation; never represented as source data."""
import unittest
from unittest.mock import patch
import duckdb
from scripts.ingest_sqlshed import audit


class AuditTests(unittest.TestCase):
    def test_wrong_count_blocks_acceptance(self):
        con = duckdb.connect()
        con.execute('CREATE TABLE categories(category_id INTEGER)')
        con.execute('INSERT INTO categories VALUES (1)')
        with self.assertRaisesRegex(RuntimeError, 'Row count or primary key'):
            audit(con)
        con.close()

    def test_duplicate_and_null_keys_block_acceptance(self):
        for values in ('(1),(1)', '(1),(NULL)'):
            with self.subTest(values=values), patch('scripts.ingest_sqlshed.COUNTS', {'categories': 2}):
                con = duckdb.connect()
                con.execute('CREATE TABLE categories(category_id INTEGER)')
                con.execute('INSERT INTO categories VALUES '+values)
                with self.assertRaisesRegex(RuntimeError, 'primary key'):
                    audit(con)
                con.close()

    def test_orphan_blocks_acceptance(self):
        con = duckdb.connect()
        con.execute('CREATE TABLE categories(category_id INTEGER,parent_category_id INTEGER)')
        con.execute('INSERT INTO categories VALUES (1,99)')
        with patch('scripts.ingest_sqlshed.COUNTS', {'categories': 1}), patch('scripts.ingest_sqlshed.RELATIONS', [('categories','parent_category_id','categories','category_id')]):
            with self.assertRaisesRegex(RuntimeError, 'Orphan'):
                audit(con)
        con.close()


if __name__ == '__main__':
    unittest.main()
