"""Provision the dedicated detail login with only approved column privileges."""
import os
from secrets import token_hex

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from dataquery_agent.detail_templates import DETAIL_ROLE
from dataquery_agent.metrics import ROOT

GRANTS = {
    "dwd.payment_attempt_fact": ("payment_id", "order_id", "paid_at", "status"),
    "dwd.order_fact": ("order_id", "channel"),
    "dwd.order_item_fact": ("order_id", "product_id", "order_purchase_time", "net_item_amount"),
    "dim.product": ("product_id", "category_id"),
}


def provision_reader(path, reader_role, grants, config_key):
    if path.exists():
        raise SystemExit("Detail reader credentials already exist.")
    admin = dotenv_values(ROOT / ".env")
    password = token_hex(32)
    role = sql.Identifier(reader_role)
    written = False
    try:
        with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed",
                             user=admin["POSTGRES_USER"], password=admin["POSTGRES_PASSWORD"]) as pg:
            pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                               "NOREPLICATION NOBYPASSRLS PASSWORD {}").format(role, sql.Literal(password)))
            pg.execute(sql.SQL("ALTER ROLE {} IN DATABASE sqlshed SET default_transaction_read_only=on").format(role))
            pg.execute(sql.SQL("GRANT CONNECT ON DATABASE sqlshed TO {}").format(role))
            pg.execute(sql.SQL("GRANT USAGE ON SCHEMA dwd,dim TO {}").format(role))
            for name, columns in grants.items():
                pg.execute(sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(*name.split(".")), role))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written = True
            with os.fdopen(fd, "w") as config:
                config.write(f"{config_key}=postgresql://{reader_role}:{password}@127.0.0.1:5433/sqlshed\n")
    except BaseException:
        if written:
            path.unlink()
        raise
    print("Reader configured with column-level SELECT and mode 0600 credentials.")


def main():
    provision_reader(ROOT / ".env.detail-query", DETAIL_ROLE, GRANTS, "DETAIL_QUERY_DSN")


if __name__ == "__main__":
    main()
