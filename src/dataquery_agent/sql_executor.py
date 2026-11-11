import os
from dataclasses import dataclass
from typing import Any

import psycopg

from dataquery_agent.sql_guard import validate_sql


READER_ROLE = "dataquery_reader"
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 5_000
MAX_RESULT_ROWS = 100


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool


class QueryExecutionError(RuntimeError):
    pass


def execute_readonly_query(sql: str) -> QueryResult:
    allowed, reason = validate_sql(sql)
    if not allowed:
        raise ValueError(f"SQL 安全校验失败：{reason}")

    user = os.environ.get("AGENT_DB_USER")
    if user != READER_ROLE:
        raise QueryExecutionError("查询执行必须使用 dataquery_reader")

    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=5433,
            dbname=os.environ["POSTGRES_DB"],
            user=user,
            password=os.environ["AGENT_DB_PASSWORD"],
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options="-c default_transaction_read_only=on",
            autocommit=True,
        ) as connection:
            with connection.transaction(force_rollback=True):
                with connection.cursor() as control_cursor:
                    control_cursor.execute("SET TRANSACTION READ ONLY")
                    control_cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{STATEMENT_TIMEOUT_MS}ms",),
                    )
                    control_cursor.execute(
                        "SELECT current_user, "
                        "current_setting('transaction_read_only')"
                    )
                    current_user, read_only = control_cursor.fetchone()
                    if current_user != READER_ROLE or read_only != "on":
                        raise QueryExecutionError("数据库只读身份验证失败")

                with connection.cursor(name="dataquery_result") as result_cursor:
                    result_cursor.execute(sql)
                    columns = tuple(column.name for column in result_cursor.description)
                    rows = result_cursor.fetchmany(MAX_RESULT_ROWS + 1)

        return QueryResult(
            columns=columns,
            rows=tuple(rows[:MAX_RESULT_ROWS]),
            truncated=len(rows) > MAX_RESULT_ROWS,
        )
    except KeyError as error:
        raise QueryExecutionError(f"缺少数据库配置：{error.args[0]}") from None
    except psycopg.errors.QueryCanceled:
        raise QueryExecutionError("查询执行超时") from None
    except psycopg.Error:
        raise QueryExecutionError("数据库查询失败") from None


class BoundedQueryError(QueryExecutionError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def execute_bounded_readonly(dsn, statement, parameters, *, role, validate, authorize):
    """Run a vetted parameterized statement under independent connection and query limits."""
    from math import isfinite
    from psycopg.conninfo import conninfo_to_dict

    validate(statement)
    try:
        config = conninfo_to_dict(dsn)
        if config.get("user") != role or config.get("dbname") != "sqlshed":
            raise BoundedQueryError("unsafe_role")
        with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=True) as connection:
            with connection.transaction(force_rollback=True):
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL search_path = pg_catalog")
                connection.execute("SELECT set_config('statement_timeout', %s, true)", (f"{STATEMENT_TIMEOUT_MS}ms",))
                identity = connection.execute(
                    "SELECT session_user, current_user, current_database(), current_setting('transaction_read_only')"
                ).fetchone()
                if identity != (role, role, "sqlshed", "on"):
                    raise BoundedQueryError("unsafe_role")
                authorize(connection)
                plan = connection.execute("EXPLAIN (FORMAT JSON) " + statement, parameters).fetchone()[0]
                cost = float(plan[0]["Plan"]["Total Cost"])
                if not isfinite(cost) or cost > 100_000:
                    raise BoundedQueryError("plan_cost_limit")
                with connection.cursor(name="controlled_detail_result") as cursor:
                    cursor.execute(statement, parameters)
                    rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
                    columns = tuple(column.name for column in cursor.description)
                return QueryResult(columns, tuple(rows[:MAX_RESULT_ROWS]), len(rows) > MAX_RESULT_ROWS)
    except psycopg.errors.QueryCanceled:
        raise BoundedQueryError("query_timeout") from None
    except psycopg.errors.InsufficientPrivilege:
        raise BoundedQueryError("access_denied") from None
    except psycopg.errors.UndefinedColumn:
        raise BoundedQueryError("schema_drift") from None
    except psycopg.Error:
        raise BoundedQueryError("database_unavailable") from None
