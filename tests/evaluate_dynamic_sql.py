"""Run fixed or Gemini SQL proposals through Agent, and independently reconcile with ODS."""
import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values

from dataquery_agent.agent_prompts import schema_for_prompt
from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.dynamic_context import compile_blocks
from dataquery_agent.dynamic_requirements import recognize
from dataquery_agent.dynamic_sql_engine import DynamicSqlEngine
from dataquery_agent.intent_semantic_resolver import gemini_generator
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.unified_agent import UnifiedAgent
from evaluate_business_planning import reference_connection

FIXTURE = json.loads((ROOT / "tests/fixtures/dynamic_sql_v1.json").read_text())
TODAY = date(2025, 3, 10)


def context_from_prompt(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("上下文：", 1)[1])[0]


def fixed_candidate(prompt, change=None):
    context = context_from_prompt(prompt)
    statement = compile_blocks(context["blocks"])
    if change:
        statement = change(statement)
    return json.dumps({"sql": statement, "parameters": context["parameters"],
                       "sources": list(context["models"]), "output_fields": context["output_fields"],
                       "steps": [], "unresolved": []}, ensure_ascii=False)


def ground_truth(pg, requirement):
    params = requirement.parameters()
    if requirement.operation in {"attempts_by_order", "attempt_rank_by_channel"}:
        rows = pg.execute("""
            SELECT o.channel, p.order_id, count(*) AS attempts
            FROM ods.payments p JOIN ods.orders o USING(order_id)
            WHERE p.paid_at >= %(start)s AND p.paid_at < %(end)s
              AND (%(status)s::text IS NULL OR p.status = %(status)s)
            GROUP BY o.channel, p.order_id
        """, params | {"status": requirement.status}).fetchall()
        if requirement.operation == "attempts_by_order":
            output = [{"order_id": row["order_id"], "attempt_count": row["attempts"]}
                      for row in sorted(rows, key=lambda row: row["order_id"])
                      if row["attempts"] > requirement.threshold]
        else:
            output = []
            for channel in sorted({row["channel"] for row in rows}):
                ordered = sorted((row for row in rows if row["channel"] == channel),
                                 key=lambda row: (-row["attempts"], row["order_id"]))
                output.extend(
                    {"channel": channel, "order_id": row["order_id"], "attempt_count": row["attempts"], "rank": rank}
                    for rank, row in enumerate(ordered[:requirement.top_n], 1))
    else:
        raw = pg.execute("""
            SELECT p.category_id, i.order_id
            FROM ods.returns r JOIN ods.order_items i USING(order_item_id)
            JOIN ods.products p USING(product_id)
            WHERE r.returned_at >= %(start)s AND r.returned_at < %(end)s
        """, params)
        groups = {}
        for row in raw:
            groups.setdefault(row["category_id"], set()).add(row["order_id"])
        output = [{"category_id": key, "order_count": len(value)} for key, value in sorted(groups.items())]
    return output[:100], len(output) > 100


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "live"), default="fixed")
    mode = parser.parse_args().mode
    engine = DynamicSqlEngine.local()
    resolver = CapabilityResolver(CapabilityRegistry(engine.snapshot()))
    agent = UnifiedAgent(MetricQueryService(None), resolver, dynamic_engine=engine)
    generate = gemini_generator(dotenv_values(ROOT / ".env"), include_usage=True,
                               schema_for_prompt=schema_for_prompt) if mode == "live" else fixed_candidate
    cases = []
    with reference_connection() as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        pg.execute("SET LOCAL statement_timeout = '15s'")
        for case in FIXTURE["live_cases"]:
            question = case["question"]
            requirement = recognize(question, TODAY)
            started = monotonic()
            answer = agent.answer(question, generate, today=TODAY)
            expected, truncated = ground_truth(pg, requirement)
            result = answer["result"]
            executed = result.get("status") == "ok"
            numeric_equal = executed and result["results"] == expected and result["truncated"] == truncated
            valid = executed and result["execution_trace"]["sql_validated"]
            cases.append({
                "id": case["id"], "question": question, "requirement": asdict(requirement),
                "generation_passed": any(item.get("status") == "validated"
                                         for item in result.get("execution_trace", {}).get("generation_attempts", [])),
                "safety_and_semantics_passed": valid, "ods_equal": numeric_equal,
                "passed": answer["task"] == "dynamic" and numeric_equal and valid,
                "expected_rows": len(expected), "expected_truncated": truncated,
                "elapsed_ms": round((monotonic() - started) * 1000), "answer": answer,
            })
    attempts = [attempt for case in cases for attempt in case["answer"]["trace"]["model_attempts"]]
    tokens = {key: sum(attempt.get("usage", {}).get(key, 0) for attempt in attempts)
              for key in ("prompt", "output", "total")
              if any(key in attempt.get("usage", {}) for attempt in attempts)}
    print(json.dumps({
        "mode": mode, "passed": sum(case["passed"] for case in cases), "total": len(cases),
        "model_attempts": len(attempts), "tokens": tokens, "cases": cases,
    }, ensure_ascii=False, indent=2))
    return 0 if all(case["passed"] for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
