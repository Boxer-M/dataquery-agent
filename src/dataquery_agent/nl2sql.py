from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dataquery_agent.sql_executor import QueryResult, execute_readonly_query
from dataquery_agent.sql_guard import validate_sql


@dataclass(frozen=True)
class Nl2SqlResult:
    question: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool


class SchemaReadError(RuntimeError):
    pass


class SqlGenerationError(RuntimeError):
    pass


class SqlValidationError(ValueError):
    pass


def answer_question(
    question: str,
    *,
    schema_reader: Callable[[], str],
    prompt_builder: Callable[[str, str], str],
    sql_generator: Callable[[str], str | None],
    executor: Callable[[str], QueryResult] = execute_readonly_query,
) -> Nl2SqlResult:
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("用户问题不能为空")

    try:
        schema_context = schema_reader()
    except Exception:
        raise SchemaReadError("Schema 读取失败") from None

    prompt = prompt_builder(normalized_question, schema_context)
    try:
        model_text = sql_generator(prompt)
    except Exception:
        raise SqlGenerationError("Gemini SQL 生成失败") from None

    if not model_text or not model_text.strip():
        raise SqlGenerationError("模型未返回 SQL 文本")

    sql = model_text.strip()
    if "```" in sql:
        raise SqlGenerationError("模型返回了 Markdown 代码块")

    allowed, reason = validate_sql(sql)
    if not allowed:
        raise SqlValidationError(f"SQL 安全校验失败：{reason}")

    query_result = executor(sql)
    return Nl2SqlResult(
        question=normalized_question,
        sql=sql,
        columns=query_result.columns,
        rows=query_result.rows,
        truncated=query_result.truncated,
    )
