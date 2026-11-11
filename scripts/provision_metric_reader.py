"""Create the dedicated local metric login and save its password outside Git."""
import os
from secrets import token_hex

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from dataquery_agent.dws import models
from dataquery_agent.metric_query_service import ROLE
from dataquery_agent.metrics import ROOT


def main():
    path = ROOT / ".env.metric-query"
    if path.exists():
        raise SystemExit("Credential file already exists. Leave the existing login in place.")
    admin = dotenv_values(ROOT / ".env")
    password = token_hex(32)
    role = sql.Identifier(ROLE)
    written = False
    try:
        with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed",
                             user=admin["POSTGRES_USER"], password=admin["POSTGRES_PASSWORD"]) as pg:
            pg.execute(sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(role, sql.Literal(password)))
            pg.execute(sql.SQL("ALTER ROLE {} IN DATABASE sqlshed SET default_transaction_read_only=on").format(role))
            pg.execute(sql.SQL("GRANT CONNECT ON DATABASE sqlshed TO {}").format(role))
            pg.execute(sql.SQL("GRANT USAGE ON SCHEMA dws,dim TO {}").format(role))
            tables = [sql.Identifier(*item["name"].split(".")) for item in models()["models"]]
            pg.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.SQL(",").join(tables), role))
            pg.execute(sql.SQL("GRANT SELECT (product_id,category_id) ON dim.product TO {}").format(role))
            pg.execute(sql.SQL("GRANT SELECT (customer_id,country,loyalty_tier) ON dim.customer TO {}").format(role))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written = True
            with os.fdopen(fd, "w") as config:
                config.write(f"METRIC_QUERY_DSN=postgresql://{ROLE}:{password}@127.0.0.1:5433/sqlshed\n")
    except BaseException:
        if written:
            path.unlink()
        raise
    print("Dedicated metric reader provisioned. Credentials saved with mode 0600.")


if __name__ == "__main__":
    main()
