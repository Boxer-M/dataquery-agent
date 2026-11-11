"""Run the approved detail routes through Gemini and reconcile with read-only ODS."""
import json
from datetime import date
from decimal import Decimal

import psycopg
from dotenv import dotenv_values

from dataquery_agent.agent_prompts import schema_for_prompt
from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.detail_executor import DetailQueryExecutor
from dataquery_agent.intent_semantic_resolver import gemini_generator
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT, read_queries
from dataquery_agent.unified_agent import UnifiedAgent
from test_detail_executor import FIXTURE, WINDOW, reader_config


def reference(pg, case):
    args = {"start": WINDOW["start_date"], "end": WINDOW["end_date"]}
    if case["analysis"] == "sales_by_order":
        rows = pg.execute("""
            SELECT i.order_id, sum(i.quantity*i.unit_price-i.discount_amount)
            FROM ods.order_items i JOIN ods.orders o USING(order_id)
            WHERE o.ordered_at >= %(start)s AND o.ordered_at < %(end)s
            GROUP BY i.order_id ORDER BY i.order_id LIMIT 101
        """, args).fetchall()
        return [{"order_id": order, "net_amount": format(Decimal(net), "f")}
                for order, net in rows[:100]], len(rows) > 100
    sequence = read_queries(ROOT / "sql/warehouse/metadata/ground_truth.sql")["payment_sequence_ods"]
    if not case["group_by"]:
        return [{"order_count": len(pg.execute(sequence, args).fetchall())}], False
    rows = pg.execute("SELECT o.channel,count(*) FROM (" + sequence.rstrip(";") +
                      ") q JOIN ods.orders o USING(order_id) GROUP BY o.channel ORDER BY o.channel",
                      args).fetchall()
    return [{"channel": channel, "order_count": count} for channel, count in rows], False


def main():
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
    executor = DetailQueryExecutor.local()
    resolver = CapabilityResolver(CapabilityRegistry(executor.snapshot()))
    agent = UnifiedAgent(service, resolver, executor)
    backend = gemini_generator(dotenv_values(ROOT / ".env"), include_usage=True,
                               schema_for_prompt=schema_for_prompt)
    cases = []
    with psycopg.connect(**reader_config(), options="-c default_transaction_read_only=on") as pg:
        pg.execute("SET LOCAL statement_timeout = '10s'")
        for case in FIXTURE["live_cases"]:
            raw_outputs = []

            def generate(prompt):
                item = backend(prompt)
                raw_outputs.append(item)
                return item

            answer = agent.answer(case["question"], generate, today=date(2025, 3, 10))
            result = answer["result"].get("detail_result", {})
            expected, truncated = reference(pg, case)
            rows = result.get("results", [])
            passed = (answer["status"] == "ok" and answer["task"] == "detail"
                      and result.get("analysis_id") == case["analysis"]
                      and rows == expected and result.get("truncated") == truncated
                      and len(answer["trace"]["tools"]) == 1)
            cases.append({
                "id": case["id"], "passed": passed, "status": answer["status"], "code": answer["code"],
                "answer_zh": answer["answer_zh"], "rows": len(rows), "truncated": result.get("truncated"),
                "sample": rows[:2], "trace": answer["trace"], "model_outputs": raw_outputs,
            })
    print(json.dumps({"passed": sum(case["passed"] for case in cases), "total": len(cases),
                      "cases": cases}, ensure_ascii=False, indent=2))
    return 0 if all(case["passed"] for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
