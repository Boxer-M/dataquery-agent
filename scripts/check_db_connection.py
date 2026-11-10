import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env", override=False)

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
        cursor.execute("SELECT current_database(), current_user;")
        database, user = cursor.fetchone()
        if user != "dataquery_reader":
            raise RuntimeError("连接检查必须使用 dataquery_reader")
        print(f"数据库：{database}")
        print(f"当前用户：{user}")
