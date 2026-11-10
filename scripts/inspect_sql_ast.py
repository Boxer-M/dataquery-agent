from sqlglot import exp, parse


sql_samples = [
    "SELECT COUNT(*) FROM public.ods_orders;",
    "DELETE FROM public.ods_orders;",
    "SELECT 1; SELECT 2;",
]

for sql in sql_samples:
    statements = parse(sql, read="postgres")
    print(f"SQL：{sql}")
    print(f"语句数量：{len(statements)}")

    for statement in statements:
        tables = [
            table.sql(dialect="postgres")
            for table in statement.find_all(exp.Table)
        ]
        print(f"语句类型：{type(statement).__name__}")
        print(f"引用表：{tables}")

    print()
