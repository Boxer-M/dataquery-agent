from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.scope import Scope, traverse_scope


ALLOWED_TABLES = frozenset(
    {
        ("ods", "customers"),
        ("ods", "geolocation"),
        ("ods", "order_items"),
        ("ods", "order_payments"),
        ("ods", "order_reviews"),
        ("ods", "orders"),
        ("ods", "products"),
        ("ods", "sellers"),
        ("ods", "product_category_name_translation"),
    }
)
FORBIDDEN_WRITE_NODES = (
    exp.Delete,
    exp.Update,
    exp.Insert,
    exp.Merge,
    exp.Into,
)
ALLOWED_FUNCTION_NODES = frozenset(
    {
        exp.Count,
        exp.Sum,
        exp.Coalesce,
    }
)


def _validate_functions(statement: exp.Select) -> tuple[bool, str]:
    for function in statement.find_all(exp.Func):
        if isinstance(function.parent, exp.Dot):
            return False, "不允许带 Schema 的函数调用"
        if isinstance(function, exp.Anonymous):
            return False, f"不允许未知函数：{function.name}"
        if type(function) not in ALLOWED_FUNCTION_NODES:
            return False, f"函数不在白名单：{type(function).__name__.upper()}"

    return True, "函数通过白名单检查"


def _validate_query_tables(statement: exp.Select) -> tuple[bool, str]:
    try:
        scopes = traverse_scope(statement)
        tables = []
        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                return False, "只允许 SELECT 查询结构"
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    tables.append(source)
                elif not isinstance(source, Scope):
                    return False, "存在无法识别的查询对象"
    except OptimizeError:
        return False, "无法可靠分析查询对象"

    if not tables:
        return False, "查询必须引用白名单业务表"

    for table in tables:
        if table.catalog:
            return False, "不允许跨数据库引用表"
        if not table.db:
            return False, "物理表必须显式指定 Schema"
        if (table.db, table.name) not in ALLOWED_TABLES:
            return False, f"表 {table.db}.{table.name} 不在白名单"

    return True, "查询对象通过白名单检查"


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

    write_node = next(
        (node for node in statement.walk() if isinstance(node, FORBIDDEN_WRITE_NODES)),
        None,
    )
    if write_node is not None:
        write_type = type(write_node).__name__.upper()
        return False, f"不允许写操作：{write_type}"

    if statement.find(exp.Lock):
        return False, "不允许 FOR UPDATE 等锁定语义"

    functions_allowed, reason = _validate_functions(statement)
    if not functions_allowed:
        return False, reason

    return _validate_query_tables(statement)
