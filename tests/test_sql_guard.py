import unittest

from dataquery_agent.sql_guard import validate_sql


class ValidateSqlTests(unittest.TestCase):
    def test_minimum_allowlist(self) -> None:
        cases = [
            ("SELECT COUNT(*) FROM ods.orders;", True, "通过"),
            ("DELETE FROM ods.orders;", False, "只允许 SELECT"),
            ("SELECT 1; SELECT 2;", False, "只允许一条"),
            ("", False, "不能为空"),
            ("SELECT (", False, "语法错误"),
            (
                "SELECT * FROM ods.orders FOR UPDATE;",
                False,
                "锁定语义",
            ),
        ]

        for sql, expected_allowed, expected_reason in cases:
            with self.subTest(sql=sql):
                allowed, reason = validate_sql(sql)
                self.assertEqual(allowed, expected_allowed)
                self.assertIn(expected_reason, reason)

    def test_query_object_allowlist(self) -> None:
        cases = [
            ("SELECT * FROM ods.orders;", True, "通过"),
            ("SELECT * FROM ods.orders AS o;", True, "通过"),
            (
                "SELECT * FROM ods.orders o "
                "JOIN ods.customers c ON o.customer_id = c.customer_id;",
                True,
                "通过",
            ),
            ("SELECT * FROM orders;", False, "Schema"),
            ("SELECT * FROM public.ods_orders;", False, "不在白名单"),
            ("SELECT * FROM ods.secret_orders;", False, "不在白名单"),
            (
                "WITH ods_orders AS (SELECT * FROM ods.orders) "
                "SELECT * FROM ods_orders;",
                True,
                "通过",
            ),
            (
                "WITH hidden AS (SELECT * FROM ods.secret_orders) "
                "SELECT * FROM hidden;",
                False,
                "不在白名单",
            ),
            (
                "SELECT * FROM (SELECT * FROM ods.secret_orders) AS hidden;",
                False,
                "不在白名单",
            ),
        ]

        for sql, expected_allowed, expected_reason in cases:
            with self.subTest(sql=sql):
                allowed, reason = validate_sql(sql)
                self.assertEqual(allowed, expected_allowed)
                self.assertIn(expected_reason, reason)

    def test_rejects_nested_write_operations(self) -> None:
        cases = [
            ("SELECT * FROM ods.orders;", True, "通过"),
            (
                "WITH source AS (SELECT * FROM ods.orders) "
                "SELECT * FROM source;",
                True,
                "通过",
            ),
            (
                "WITH changed AS (DELETE FROM ods.orders RETURNING *) "
                "SELECT * FROM changed;",
                False,
                "DELETE",
            ),
            (
                "WITH changed AS (UPDATE ods.orders "
                "SET order_status = 'canceled' RETURNING *) "
                "SELECT * FROM changed;",
                False,
                "UPDATE",
            ),
            (
                "WITH changed AS (INSERT INTO ods.orders (order_id) "
                "VALUES ('demo') RETURNING *) SELECT * FROM changed;",
                False,
                "INSERT",
            ),
            (
                "SELECT * INTO ods.new_orders FROM ods.orders;",
                False,
                "INTO",
            ),
        ]

        for sql, expected_allowed, expected_reason in cases:
            with self.subTest(sql=sql):
                allowed, reason = validate_sql(sql)
                self.assertEqual(allowed, expected_allowed)
                self.assertIn(expected_reason, reason)

    def test_function_allowlist(self) -> None:
        cases = [
            ("SELECT COUNT(*) FROM ods.orders;", True, "通过"),
            ("SELECT SUM(price) FROM ods.order_items;", True, "通过"),
            (
                "SELECT COALESCE(order_approved_at, order_purchase_timestamp) "
                "FROM ods.orders;",
                True,
                "通过",
            ),
            (
                "SELECT COALESCE(SUM(price), 0) FROM ods.order_items;",
                True,
                "通过",
            ),
            ("SELECT pg_sleep(1) FROM ods.orders;", False, "未知函数"),
            (
                "SELECT set_config('search_path', 'public', false) "
                "FROM ods.orders;",
                False,
                "未知函数",
            ),
            ("SELECT mystery(order_id) FROM ods.orders;", False, "未知函数"),
            (
                "SELECT pg_catalog.pg_sleep(1) FROM ods.orders;",
                False,
                "带 Schema",
            ),
            (
                "SELECT COALESCE(pg_sleep(1), 0) FROM ods.orders;",
                False,
                "未知函数",
            ),
            ("SELECT CAST(order_id AS TEXT) FROM ods.orders;", False, "CAST"),
            ("SELECT order_id::text FROM ods.orders;", False, "CAST"),
        ]

        for sql, expected_allowed, expected_reason in cases:
            with self.subTest(sql=sql):
                allowed, reason = validate_sql(sql)
                self.assertEqual(allowed, expected_allowed)
                self.assertIn(expected_reason, reason)


if __name__ == "__main__":
    unittest.main()
