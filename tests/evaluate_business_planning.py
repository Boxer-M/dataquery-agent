"""Evaluate fixed proposals, real PostgreSQL, or live Gemini separately."""
import argparse
import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from time import monotonic

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from dataquery_agent.business_query_planner import BusinessQueryPlanner
from dataquery_agent.dws import metric_query
from dataquery_agent.intent_semantic_resolver import gemini_generator
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT, read_queries
from test_business_query_planner import (
    CASES, TODAY, BusinessPlannerTests, expected_plan, expected_requests, proposal_output, stub_result,
)

LIVE = ("multi", "mom", "channel_top", "failure_rate", "ambiguous", "detail")
POSTGRES = ("multi", "channel_top", "periods", "mom", "failure_rate", "cross")
REFERENCE = read_queries(ROOT / "sql/warehouse/dws_metrics/ods.sql")


def reference_rows(pg, request):
    mid = request["metric_id"]
    axis = next(iter(request["group_by"]), None)
    query = metric_query(pg, REFERENCE[mid], axis)
    params = dict(start=request["start_date"], end=request["end_date"], as_of="2026-01-01",
                  channel=request["filters"].get("channel"), category=request["filters"].get("category"))
    return {row["bucket"]: row for row in pg.execute(query, params)}


def numeric_check(case, result, requests, pg):
    expected = case["expected"]
    references = [reference_rows(pg, request) for request in requests]
    rows = result["results"]
    operation = expected["operation"]
    if operation == "change":
        before = Decimal(references[0]["all"]["value"])
        after = Decimal(references[1]["all"]["value"])
        cell = rows[0]["metrics"][expected["metrics"][0]]
        return (Decimal(cell["baseline"]["value"]) == before
                and Decimal(cell["current"]["value"]) == after
                and Decimal(cell["change"]) == after - before
                and Decimal(cell["change_pct"]) == ((after - before) / before * 100).quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP))
    if operation in {"top", "filter_top"}:
        ordered = list(references[-1])
        if operation == "filter_top":
            ordered = [key for key in ordered if key in references[0] and references[0][key]["value"] > 500]
        ordered = [key for key in ordered if references[-1][key]["value"] is not None]
        ordered.sort(key=lambda key: (-references[-1][key]["value"], key))
        keys = ordered[:expected["limit"]]
    else:
        keys = ["all"]
    if [row["bucket"] for row in rows] != keys:
        return False
    for row in rows:
        for mid, reference in zip(expected["metrics"], references):
            cell = row["metrics"][mid]
            source = reference.get(row["bucket"])
            if source is None or cell is None:
                if source is not None or cell is not None:
                    return False
                continue
            for field in ("value", "numerator", "denominator"):
                if field in source:
                    value = cell.get(field)
                    actual = Decimal(value) if value is not None else None
                    if actual != source[field]:
                        return False
    return True


def evaluate_case(case, mode, service, reference, live_generator):
    expected = case["expected"]
    seen = []
    planner = BusinessQueryPlanner(service)
    execute = service.query

    def track(request):
        seen.append(request)
        return stub_result(request, case.get("tool")) if mode == "fixed" else execute(request)

    class TracedService:
        query = staticmethod(track)

    # Keep validation bound to the real service and intercept only the query call.
    planner.service = TracedService()
    planner.service.metrics = service.metrics
    provider_failure = None

    def generate(prompt):
        nonlocal provider_failure
        if mode != "live":
            return proposal_output(case)
        try:
            return live_generator(prompt)
        except Exception as error:
            status_code = getattr(error, "code", None)
            provider_failure = {"type": type(error).__name__,
                                "http_status": status_code if type(status_code) is int else None}
            raise
    began = monotonic()
    result = planner.answer(case["question"], generate, today=TODAY)
    elapsed = round((monotonic() - began) * 1000)
    plan_ok = expected_plan(case, result)
    calls_ok = seen == expected_requests(case)
    outcome_ok = result["status"] == expected["status"]
    numeric_ok = None
    if "operation" in expected:
        numeric_ok = False
        if outcome_ok and plan_ok and calls_ok:
            try:
                if mode == "fixed":
                    BusinessPlannerTests().check_operation(case, result)
                    numeric_ok = True
                else:
                    numeric_ok = numeric_check(case, result, seen, reference)
            except (AssertionError, IndexError, KeyError, TypeError):
                pass
    passed = plan_ok and calls_ok and outcome_ok and numeric_ok is not False
    failures = []
    for label, value in (("plan", plan_ok), ("tool_calls", calls_ok),
                         ("status", outcome_ok), ("numeric", numeric_ok)):
        if value is False:
            failures.append(label)
    return {
        "id": case["id"], "passed": passed, "plan_correct": plan_ok if "type" in expected else None, "tool_calls_correct": calls_ok,
        "numeric_correct": numeric_ok, "clarify_or_reject_correct": (
            outcome_ok and plan_ok and calls_ok if expected["status"] in {"clarify", "unsupported"} else None),
        "elapsed_ms": elapsed, "status": result["status"], "code": result.get("code"),
        "tool_errors": [tool["error_code"] for tool in result.get("tools", []) if "error_code" in tool],
        "failures": failures, "provider_failure": provider_failure,
        **({"demo": {"plan": result["plan"], "results": result["results"], "tools": result["tools"],
                     "answer_zh": result["answer_zh"], "trace_id": result["trace_id"]}}
           if mode == "live" and passed and case["id"] in {"multi", "mom", "channel_top"} else {}),
    }


def reference_connection():
    config = dotenv_values(ROOT / ".env.sqlshed-reader")
    return psycopg.connect(
        host=config["SQLSHED_HOST"], port=config["SQLSHED_PORT"], dbname=config["SQLSHED_DATABASE"],
        user=config["SQLSHED_USER"], password=config["SQLSHED_PASSWORD"], row_factory=dict_row,
        connect_timeout=5,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "postgres", "live"), default="fixed")
    args = parser.parse_args()
    mode = args.mode
    dsn = dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN") if mode != "fixed" else None
    service = MetricQueryService(dsn)
    if mode == "fixed":
        reports = [evaluate_case(case, mode, service, None, None) for case in CASES.values()]
    else:
        generator = gemini_generator(dotenv_values(ROOT / ".env"), retry_once=True) if mode == "live" else None
        keys = LIVE if mode == "live" else POSTGRES
        with reference_connection() as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '15s'")
            reports = [evaluate_case(CASES[key], mode, service, pg, generator) for key in keys]
    scores = {}
    for field in ("passed", "plan_correct", "tool_calls_correct", "numeric_correct", "clarify_or_reject_correct"):
        values = [report[field] for report in reports if report[field] is not None]
        scores[field] = {"passed": sum(values), "total": len(values)}
    reasons = Counter(report["code"] or ",".join(report["tool_errors"]) or ",".join(report["failures"])
                      for report in reports if not report["passed"])
    output = {
        "mode": mode, "fixture_version": 1, "scores": scores,
        "failures": dict(reasons),
        "latency_ms": {"max": max(report["elapsed_ms"] for report in reports),
                       "mean": round(sum(report["elapsed_ms"] for report in reports) / len(reports))},
        "cases": reports,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(report["passed"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
