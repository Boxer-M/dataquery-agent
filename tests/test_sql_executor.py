import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import psycopg

from dataquery_agent.sql_executor import QueryExecutionError, execute_readonly_query


VALID_SQL = "SELECT order_id FROM public.ods_orders;"
TEST_ENV = {
    "POSTGRES_DB": "dataquery",
    "AGENT_DB_USER": "dataquery_reader",
    "AGENT_DB_PASSWORD": "test-only-password",
}


def configure_connection(mock_connect: MagicMock):
    connection_context = mock_connect.return_value
    connection = connection_context.__enter__.return_value
    transaction_context = connection.transaction.return_value

    control_context = MagicMock()
    control_cursor = control_context.__enter__.return_value
    control_cursor.fetchone.return_value = ("dataquery_reader", "on")

    result_context = MagicMock()
    result_cursor = result_context.__enter__.return_value
    result_cursor.description = [SimpleNamespace(name="order_id")]
    result_cursor.fetchmany.return_value = [(str(index),) for index in range(101)]
    connection.cursor.side_effect = [control_context, result_context]

    return connection, transaction_context, control_cursor, result_cursor


class ExecuteReadonlyQueryTests(unittest.TestCase):
    @patch("dataquery_agent.sql_executor.psycopg.connect")
    def test_limits_rows_and_configures_readonly_transaction(self, mock_connect):
        connection, _, control_cursor, result_cursor = configure_connection(
            mock_connect
        )

        with patch.dict(os.environ, TEST_ENV, clear=True):
            result = execute_readonly_query(VALID_SQL)

        mock_connect.assert_called_once_with(
            host="127.0.0.1",
            port=5433,
            dbname="dataquery",
            user="dataquery_reader",
            password="test-only-password",
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
            autocommit=True,
        )
        connection.transaction.assert_called_once_with(force_rollback=True)
        control_cursor.execute.assert_has_calls(
            [
                call("SET TRANSACTION READ ONLY"),
                call(
                    "SELECT set_config('statement_timeout', %s, true)",
                    ("5000ms",),
                ),
                call(
                    "SELECT current_user, "
                    "current_setting('transaction_read_only')"
                ),
            ]
        )
        result_cursor.fetchmany.assert_called_once_with(101)
        self.assertEqual(result.columns, ("order_id",))
        self.assertEqual(len(result.rows), 100)
        self.assertTrue(result.truncated)

    @patch("dataquery_agent.sql_executor.psycopg.connect")
    def test_rejects_unsafe_sql_before_connecting(self, mock_connect):
        with self.assertRaisesRegex(ValueError, "SQL 安全校验失败"):
            execute_readonly_query("DELETE FROM public.ods_orders;")

        mock_connect.assert_not_called()

    @patch("dataquery_agent.sql_executor.psycopg.connect")
    def test_rejects_wrong_configured_or_database_user(self, mock_connect):
        with patch.dict(os.environ, {**TEST_ENV, "AGENT_DB_USER": "admin"}, clear=True):
            with self.assertRaisesRegex(QueryExecutionError, "dataquery_reader"):
                execute_readonly_query(VALID_SQL)
        mock_connect.assert_not_called()

        _, _, control_cursor, _ = configure_connection(mock_connect)
        control_cursor.fetchone.return_value = ("admin", "on")
        with patch.dict(os.environ, TEST_ENV, clear=True):
            with self.assertRaisesRegex(QueryExecutionError, "身份验证失败"):
                execute_readonly_query(VALID_SQL)

    @patch("dataquery_agent.sql_executor.psycopg.connect")
    def test_rejects_transaction_that_is_not_readonly(self, mock_connect):
        _, _, control_cursor, _ = configure_connection(mock_connect)
        control_cursor.fetchone.return_value = ("dataquery_reader", "off")

        with patch.dict(os.environ, TEST_ENV, clear=True):
            with self.assertRaisesRegex(QueryExecutionError, "身份验证失败"):
                execute_readonly_query(VALID_SQL)

    @patch("dataquery_agent.sql_executor.psycopg.connect")
    def test_hides_timeout_details_and_cleans_up_contexts(self, mock_connect):
        _, transaction_context, _, result_cursor = configure_connection(mock_connect)
        result_cursor.execute.side_effect = psycopg.errors.QueryCanceled("secret detail")

        with patch.dict(os.environ, TEST_ENV, clear=True):
            with self.assertRaisesRegex(QueryExecutionError, "查询执行超时") as caught:
                execute_readonly_query(VALID_SQL)

        self.assertNotIn("secret detail", str(caught.exception))
        transaction_context.__exit__.assert_called_once()
        mock_connect.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
