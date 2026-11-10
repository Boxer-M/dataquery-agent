import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


ODS_TABLES = (
    "customers",
    "geolocation",
    "order_items",
    "order_payments",
    "order_reviews",
    "orders",
    "product_category_name_translation",
    "products",
    "sellers",
)


def get_ods_schema() -> str:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    with psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["AGENT_DB_USER"],
        password=os.environ["AGENT_DB_PASSWORD"],
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user;")
            if cursor.fetchone()[0] != "dataquery_reader":
                raise RuntimeError("Schema 读取必须使用 dataquery_reader")

            cursor.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = ANY(%s)
                ORDER BY table_name, ordinal_position
                """,
                ("ods", list(ODS_TABLES)),
            )
            rows = cursor.fetchall()
            found_tables = {table_name for table_name, _, _ in rows}
            if found_tables != set(ODS_TABLES):
                raise RuntimeError("ODS Schema 缺少预期业务表")

            lines = [
                "数据库方言：PostgreSQL",
                "Schema：ods",
            ]
            current_table = None
            for table_name, column_name, data_type in rows:
                if table_name != current_table:
                    lines.extend([f"表：ods.{table_name}", "字段："])
                    current_table = table_name
                lines.append(f"- {column_name}: {data_type}")
            schema_context = "\n".join(lines)
            return schema_context


if __name__ == "__main__":
    print(get_ods_schema())
