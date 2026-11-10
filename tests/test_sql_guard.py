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


if __name__ == "__main__":
    unittest.main()
