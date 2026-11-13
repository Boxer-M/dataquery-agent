"""Set up pgvector and separate local reader and ingestion credentials."""
import os
from secrets import token_hex

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.store import ADMIN, READER, CHUNKS, DOCS, VERSIONS


def main():
    config = dotenv_values(ROOT / ".env")
    created = []
    try:
        with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed", connect_timeout=5,
                             user=config["POSTGRES_USER"], password=config["POSTGRES_PASSWORD"]) as pg:
            pg.execute("SELECT pg_advisory_xact_lock(hashtextextended('knowledge-migration',0))")
            for name, filename in ((ADMIN, ".env.knowledge-admin"), (READER, ".env.knowledge")):
                path = ROOT / filename
                exists = pg.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
                if bool(exists) != path.exists():
                    raise RuntimeError("Knowledge role and local credentials are out of sync.")
                if exists:
                    continue
                password = token_hex(32)
                pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                   "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                                       sql.Identifier(name), sql.Literal(password)))
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
                    created.append(path)
                    output.write(f"KNOWLEDGE_DSN=postgresql://{name}:{password}@127.0.0.1:5433/sqlshed\n")
            pg.execute((ROOT / "sql/agent_meta/003_business_knowledge.sql").read_text())
            pg.execute(f"""GRANT CONNECT ON DATABASE sqlshed TO {ADMIN}, {READER};
                GRANT USAGE ON SCHEMA agent_meta, rag_vector TO {ADMIN}, {READER};
                GRANT SELECT ON {DOCS}, {VERSIONS}, {CHUNKS} TO {ADMIN}, {READER};
                GRANT INSERT ON {DOCS}, {VERSIONS}, {CHUNKS} TO {ADMIN};
                GRANT UPDATE (active_version,active_index,deleted_at) ON {DOCS} TO {ADMIN};""")
            print(pg.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0])
    except BaseException:
        for path in created:
            path.unlink()
        raise


if __name__ == "__main__":
    main()
