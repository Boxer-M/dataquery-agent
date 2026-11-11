"""Apply the management migration and provision separate service credentials."""
import os
from secrets import token_hex

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from dataquery_agent.metrics import ROOT

ROLE = "sqlshed_agent_meta"


def main():
    path = ROOT / ".env.agent-meta"
    if path.exists():
        raise SystemExit("Management credentials already exist.")
    admin = dotenv_values(ROOT / ".env")
    password, owner_token = token_hex(32), token_hex(32)
    role = sql.Identifier(ROLE)
    written = False
    try:
        with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed",
                             user=admin["POSTGRES_USER"], password=admin["POSTGRES_PASSWORD"]) as pg:
            pg.execute((ROOT / "sql/agent_meta/001_conversation.sql").read_text())
            pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                               "NOREPLICATION NOBYPASSRLS PASSWORD {}").format(role, sql.Literal(password)))
            pg.execute(sql.SQL("GRANT CONNECT ON DATABASE sqlshed TO {}").format(role))
            pg.execute(sql.SQL("GRANT USAGE ON SCHEMA agent_meta TO {}").format(role))
            pg.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON agent_meta.conversations, "
                               "agent_meta.messages TO {}").format(role))
            pg.execute(sql.SQL("GRANT SELECT, INSERT ON agent_meta.query_history TO {}").format(role))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written = True
            with os.fdopen(fd, "w") as config:
                config.write(f"AGENT_META_DSN=postgresql://{ROLE}:{password}@127.0.0.1:5433/sqlshed\n"
                             f"AGENT_OWNER_TOKEN={owner_token}\n")
    except BaseException:
        if written:
            path.unlink()
        raise
    print("Management schema and separate role initialized.")


if __name__ == "__main__":
    main()
