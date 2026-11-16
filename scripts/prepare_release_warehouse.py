"""Build or verify the local SQL Shed demo warehouse from its imported ODS."""
import psycopg
from dotenv import dotenv_values

from dataquery_agent.ads import build_ads
from dataquery_agent.dwd import build_order_item_fact
from dataquery_agent.dws import build_dws
from dataquery_agent.metrics import ROOT
from dataquery_agent.warehouse import build_batch_a


def main():
    config = dotenv_values(ROOT / ".env")
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed", autocommit=True, connect_timeout=5,
                         user=config["POSTGRES_USER"], password=config["POSTGRES_PASSWORD"]) as pg:
        for build in (build_order_item_fact, build_batch_a, build_dws, build_ads):
            build(pg)
            print(f"{build.__name__} verified", flush=True)


if __name__ == "__main__":
    main()
