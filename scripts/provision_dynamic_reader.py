"""Create the separate least-privilege login for generated SQL."""
from provision_detail_reader import provision_reader

from dataquery_agent.dynamic_sql_engine import DYNAMIC_ROLE
from dataquery_agent.metrics import ROOT

GRANTS = {
    "dwd.payment_attempt_fact": ("order_id", "paid_at", "status"),
    "dwd.order_fact": ("order_id", "channel"),
    "dwd.return_record_fact": ("order_id", "product_id", "returned_at"),
    "dim.product": ("product_id", "category_id"),
}

if __name__ == "__main__":
    provision_reader(ROOT / ".env.dynamic-query", DYNAMIC_ROLE, GRANTS, "DYNAMIC_QUERY_DSN")
