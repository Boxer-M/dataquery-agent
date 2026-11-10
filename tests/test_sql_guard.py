import unittest

from dataquery_agent.sql_guard import validate_sql


class ValidateSqlTests(unittest.TestCase):
    def test_minimum_allowlist(self) -> None:
        cases = [
            ("SELECT COUNT(*) FROM public.ods_orders;", True, "通过"),
            ("DELETE FROM public.ods_orders;", False, "只允许 SELECT"),
            ("SELECT 1; SELECT 2;", False, "只允许一条"),
            ("", False, "不能为空"),
            ("SELECT (", False, "语法错误"),
            (
                "SELECT * FROM public.ods_orders FOR UPDATE;",
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
            ("SELECT * FROM public.ods_orders;", True, "通过"),
            ("SELECT * FROM public.ods_orders AS o;", True, "通过"),
            (
                "SELECT * FROM public.ods_orders o "
                "JOIN public.ods_customers c ON o.customer_id = c.customer_id;",
                True,
                "通过",
            ),
            ("SELECT * FROM ods_orders;", False, "Schema"),
            ("SELECT * FROM public.secret_orders;", False, "不在白名单"),
            (
                "WITH ods_orders AS (SELECT * FROM public.ods_orders) "
                "SELECT * FROM ods_orders;",
                True,
                "通过",
            ),
            (
                "WITH hidden AS (SELECT * FROM public.secret_orders) "
                "SELECT * FROM hidden;",
                False,
                "不在白名单",
            ),
            (
                "SELECT * FROM (SELECT * FROM public.secret_orders) AS hidden;",
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
            ("SELECT * FROM public.ods_orders;", True, "通过"),
            (
                "WITH source AS (SELECT * FROM public.ods_orders) "
                "SELECT * FROM source;",
                True,
                "通过",
            ),
            (
                "WITH changed AS (DELETE FROM public.ods_orders RETURNING *) "
                "SELECT * FROM changed;",
                False,
                "DELETE",
            ),
            (
                "WITH changed AS (UPDATE public.ods_orders "
                "SET order_status = 'canceled' RETURNING *) "
                "SELECT * FROM changed;",
                False,
                "UPDATE",
            ),
            (
                "WITH changed AS (INSERT INTO public.ods_orders (order_id) "
                "VALUES ('demo') RETURNING *) SELECT * FROM changed;",
                False,
                "INSERT",
            ),
            (
                "SELECT * INTO public.new_orders FROM public.ods_orders;",
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
            ("SELECT COUNT(*) FROM public.ods_orders;", True, "通过"),
            ("SELECT SUM(price) FROM public.ods_order_items;", True, "通过"),
            (
                "SELECT COALESCE(order_approved_at, order_purchase_timestamp) "
                "FROM public.ods_orders;",
                True,
                "通过",
            ),
            (
                "SELECT COALESCE(SUM(price), 0) FROM public.ods_order_items;",
                True,
                "通过",
            ),
            ("SELECT pg_sleep(1) FROM public.ods_orders;", False, "未知函数"),
            (
                "SELECT set_config('search_path', 'public', false) "
                "FROM public.ods_orders;",
                False,
                "未知函数",
            ),
            ("SELECT mystery(order_id) FROM public.ods_orders;", False, "未知函数"),
            (
                "SELECT pg_catalog.pg_sleep(1) FROM public.ods_orders;",
                False,
                "带 Schema",
            ),
            (
                "SELECT COALESCE(pg_sleep(1), 0) FROM public.ods_orders;",
                False,
                "未知函数",
            ),
            ("SELECT CAST(order_id AS TEXT) FROM public.ods_orders;", False, "CAST"),
            ("SELECT order_id::text FROM public.ods_orders;", False, "CAST"),
        ]

        for sql, expected_allowed, expected_reason in cases:
            with self.subTest(sql=sql):
                allowed, reason = validate_sql(sql)
                self.assertEqual(allowed, expected_allowed)
                self.assertIn(expected_reason, reason)


if __name__ == "__main__":
    unittest.main()
