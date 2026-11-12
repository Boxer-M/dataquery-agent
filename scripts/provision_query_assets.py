"""Apply the repeatable asset migration and provision two least-privilege logins."""
import os
from secrets import token_hex

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from dataquery_agent.metrics import ROOT
from dataquery_agent.query_asset_store import ADMIN, READER

ROLES = {ADMIN: ".env.query-asset-admin", READER: ".env.query-assets"}


def main():
    config = dotenv_values(ROOT / ".env")
    created = []
    try:
        with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed", connect_timeout=5,
                             user=config["POSTGRES_USER"], password=config["POSTGRES_PASSWORD"]) as pg:
            pg.execute("SELECT pg_advisory_xact_lock(hashtextextended('query-asset-migration', 0))")
            pg.execute((ROOT / "sql/agent_meta/002_query_assets.sql").read_text())
            for name, filename in ROLES.items():
                path = ROOT / filename
                role = sql.Identifier(name)
                exists = pg.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
                if path.exists() != bool(exists):
                    raise RuntimeError("Asset role and local credential are out of sync.")
                if not exists:
                    password = token_hex(32)
                    pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                       "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                                           role, sql.Literal(password)))
                    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
                        created.append(path)
                        file.write(f"QUERY_ASSET_DSN=postgresql://{name}:{password}@127.0.0.1:5433/sqlshed\n")
                pg.execute(sql.SQL("GRANT CONNECT ON DATABASE sqlshed TO {}").format(role))
                pg.execute(sql.SQL("GRANT USAGE ON SCHEMA agent_meta TO {}").format(role))
                pg.execute(sql.SQL("GRANT SELECT ON agent_meta.query_asset_versions TO {}").format(role))
            admin = sql.Identifier(ADMIN)
            pg.execute(sql.SQL("""
                GRANT INSERT, UPDATE (lifecycle_status, validation_evidence, published_at, updated_at)
                    ON agent_meta.query_asset_versions TO {};
                GRANT SELECT, INSERT ON agent_meta.query_asset_events TO {};
                GRANT USAGE ON SEQUENCE agent_meta.query_asset_events_event_id_seq TO {};
            """).format(admin, admin, admin))
    except BaseException:
        for path in created:
            path.unlink()
        raise
    print("Asset migration and roles ready.")


if __name__ == "__main__":
    main()
