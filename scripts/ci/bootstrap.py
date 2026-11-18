"""Provision a disposable Compose environment using existing ingestion/builders."""
import os
from pathlib import Path
from secrets import token_hex
import subprocess
import sys

import psycopg
from psycopg import sql

from dataquery_agent.metrics import ROOT


def write_config(name, values):
    with os.fdopen(os.open(ROOT / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write("".join(f"{key}={value}\n" for key, value in values.items()))


def command(script):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True, cwd=ROOT)


def bootstrap():
    if os.getenv("CI_ISOLATED") != "1" or ROOT != Path("/app") or (ROOT / ".env").exists():
        raise RuntimeError("Requires a fresh isolated /app container; existing configuration is refused")
    user, password = os.environ["CI_POSTGRES_USER"], os.environ["CI_POSTGRES_PASSWORD"]
    write_config(".env", {"POSTGRES_DB": "olist", "POSTGRES_USER": user, "POSTGRES_PASSWORD": password,
                          "GEMINI_API_KEY": os.getenv("CI_GEMINI_KEY", ""),
                          "GEMINI_MODEL": "models/gemini-3.5-flash-lite"})
    write_config(".env.api", {"DATAQUERY_HTTP_TOKEN": token_hex(32)})
    command("download_sqlshed.py")
    # Reuse import validation without the CLI's separate pre-existing Olist prerequisite.
    from scripts.ingest_sqlshed import source_connection, audit, load
    source = source_connection()
    audit(source)
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="postgres", user=user,
                         password=password, autocommit=True) as pg:
        pg.execute("CREATE DATABASE sqlshed")
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed", user=user,
                         password=password, autocommit=True) as pg:
        load(pg, source)
    source.close()
    command("prepare_release_warehouse.py")
    reader_password = token_hex(32)
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed", user=user, password=password) as pg:
        pg.execute("REVOKE TEMPORARY ON DATABASE sqlshed FROM PUBLIC")
        pg.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        pg.execute(sql.SQL("CREATE ROLE sqlshed_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                           "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Literal(reader_password)))
        pg.execute("GRANT CONNECT ON DATABASE sqlshed TO sqlshed_reader")
        pg.execute("GRANT USAGE ON SCHEMA ods,dwd,dim,dws,ads TO sqlshed_reader")
        for schema in ("ods", "dwd", "dim", "dws", "ads"):
            pg.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO sqlshed_reader").format(sql.Identifier(schema)))
    write_config(".env.sqlshed-reader", {"SQLSHED_HOST": "127.0.0.1", "SQLSHED_PORT": "5433",
                                         "SQLSHED_DATABASE": "sqlshed", "SQLSHED_USER": "sqlshed_reader",
                                         "SQLSHED_PASSWORD": reader_password})
    for name in ("metric_reader", "detail_reader", "dynamic_reader", "agent_meta", "query_assets", "knowledge", "hitl"):
        command(f"provision_{name}.py")
    subprocess.run([sys.executable, "-m", "dataquery_agent.rag.pdf_cli", "init-key"], check=True)
    from dataquery_agent.rag.documents import catalog_documents
    from dataquery_agent.metric_query_service import MetricQueryService
    from dataquery_agent.rag.store import KnowledgeStore
    from evaluate_business_knowledge import FixedEmbedding
    writer = KnowledgeStore.local(admin=True)
    for document in catalog_documents(MetricQueryService(None)):
        writer.index(document, FixedEmbedding(), external_approved=True)
    print("Disposable SQL Shed, read-only roles, checkpoints and fixed-vector Catalog ready", flush=True)


if __name__ == "__main__":
    bootstrap()
