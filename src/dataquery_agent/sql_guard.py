from sqlglot import exp, parse
from sqlglot.errors import ParseError


def validate_sql(sql: str) -> tuple[bool, str]:
    if not sql.strip():
        return False, "SQL 不能为空"

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return False, "SQL 语法错误"

    if len(statements) != 1:
        return False, "只允许一条 SQL 语句"

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        return False, "只允许 SELECT 语句"

    if statement.find(exp.Lock):
        return False, "不允许 FOR UPDATE 等锁定语义"

    return True, "通过最小 SQL 白名单检查"
