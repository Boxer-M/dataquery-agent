"""Add only the owner preference table to the existing management schema."""
import psycopg
from dotenv import dotenv_values

from dataquery_agent.metrics import ROOT


def main():
    config = dotenv_values(ROOT / ".env")
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed", connect_timeout=5,
                         user=config["POSTGRES_USER"], password=config["POSTGRES_PASSWORD"]) as pg:
        pg.execute((ROOT / "sql/agent_meta/006_preferences.sql").read_text())
    print("Owner preference table ready; warehouse data and role attributes unchanged.")


if __name__ == "__main__":
    main()
