"""Initialize the isolated LangGraph checkpoint schema and saver tables."""
import psycopg
from dotenv import dotenv_values

from dataquery_agent.conversation_store import ConversationStore
from dataquery_agent.hitl import checkpointer
from dataquery_agent.metrics import ROOT


if __name__ == "__main__":
    admin = dotenv_values(ROOT / ".env")
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="sqlshed",
                         user=admin["POSTGRES_USER"], password=admin["POSTGRES_PASSWORD"]) as pg:
        pg.execute((ROOT / "sql/agent_meta/005_hitl.sql").read_text())
    store = ConversationStore(dotenv_values(ROOT / ".env.agent-meta").get("AGENT_META_DSN"))
    with checkpointer(store) as saver:
        saver.setup()
    print("HITL checkpoint schema initialized.")
