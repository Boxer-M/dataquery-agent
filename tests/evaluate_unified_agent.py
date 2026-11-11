"""Evaluate fixed, live database and live Gemini paths independently."""
import argparse
import json
from decimal import Decimal
from time import monotonic

from dotenv import dotenv_values

from dataquery_agent.agent_prompts import schema_for_prompt
from dataquery_agent.capability_registry import CapabilityRegistry
from dataquery_agent.capability_resolver import CapabilityResolver
from dataquery_agent.intent_semantic_resolver import gemini_generator
from dataquery_agent.metadata_service import MetadataService
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.unified_agent import UnifiedAgent
from evaluate_business_planning import numeric_check, reference_connection, reference_rows
from test_business_query_planner import BusinessPlannerTests
from test_metadata_capability import fixture_snapshot
from test_unified_agent import BUSINESS, CASES, TODAY, UnifiedAgentTests, expected_calls, fixed_generate, question


def numeric_correct(case, result, reference):
    requests = expected_calls(case)
    payload = result["result"]
    if "business" in case:
        business_case = BUSINESS[case["business"]]
        if reference is None:
            BusinessPlannerTests().check_operation(business_case, payload)
            return True
        return numeric_check(business_case, payload, requests, reference)
    rows = payload["query_result"]["results"]
    if reference is None:
        mid = requests[0]["metric_id"]
        return rows[0]["value"] == ("0.50000000" if mid in {
            "payment_attempt_success_rate", "item_return_rate"} else "123")
    params = {"group_by": [], "filters": {}} | requests[0]
    expected = reference_rows(reference, params)
    if set(expected) != {row["bucket"] for row in rows}:
        return False
    return all(
        (Decimal(row[key]) if row.get(key) is not None else None) == expected[row["bucket"]][key]
        for row in rows for key in ("value", "numerator", "denominator") if key in expected[row["bucket"]]
    )


def evaluate(case, mode, service, registry, reference, generate):
    agent = UnifiedAgent(service, CapabilityResolver(registry))
    if mode == "fixed":
        fixture = UnifiedAgentTests()
        fixture.service, fixture.registry = service, registry
        service.query = lambda request: fixture.fake_query(case, request)
    started = monotonic()
    actual = agent.answer(question(case), generate if mode == "live" else
                          lambda prompt: fixed_generate(case, prompt), today=TODAY)
    elapsed = round((monotonic() - started) * 1000)
    tools = actual["trace"]["tools"]
    routes = actual["routes"]
    target = expected_calls(case)
    target_ids = {request["metric_id"] for request in target}
    match = actual["match"] or {}
    status = (actual["status"], actual["code"]) == (case["status"], case.get("code"))
    route_match = (
        len(routes) == len(target) and all(
            route["status"] == "STANDARD_METRIC"
            and route["selected_source"] == registry.standard[request["metric_id"]]["source"]
            and route["metric_request"] == request
            for route, request in zip(routes, target))
        if target else
        bool(routes and routes[0].get("execution_policy") == "plan_only"
             and routes[0].get("selected_source") == case["source"])
        if "source" in case else all(route.get("selected_source") is None for route in routes)
    )
    calls_match = [tool["request"] for tool in tools] == target
    provenance = all(
        any(tool["capability_trace_id"] == route["trace_id"]
            and (tool["status"] != "ok" or (
                tool["trace_id"] and set(tool["sources"]) == route["required_fields"].keys()))
            for route in routes)
        for tool in tools
    )
    numeric = None
    if case["status"] == "ok":
        numeric = False
        if status and calls_match and provenance:
            try:
                numeric = numeric_correct(case, actual, reference)
            except (AssertionError, KeyError, IndexError, TypeError):
                pass
    scores = {
        "task_correct": actual["task"] == case["task"],
        "candidate_recall": target_ids <= set(match.get("candidate_ids", [])) if target_ids else None,
        "metric_selection": target_ids == set(match.get("confirmed", [])) if target_ids else None,
        "ambiguity_correct": status and not tools and bool(actual["choices"])
                             if case.get("code") in {"ambiguous_metric", "unconfirmed_metric"} else None,
        "route_correct": route_match,
        "tool_calls_correct": calls_match and provenance,
        "numeric_correct": numeric,
        "end_to_end": status and route_match and calls_match and provenance and numeric is not False,
    }
    passed = all(value is not False for value in scores.values())
    return {
        "id": case["id"], "passed": passed, "scores": scores, "status": actual["status"],
        "code": actual["code"], "task": actual["task"], "elapsed_ms": elapsed,
        "trace": actual["trace"], "match": actual["match"],
        "selected_sources": [route.get("selected_source") for route in routes],
        **({"example": actual["result"]} if mode == "live" and passed else {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "postgres", "live"), default="fixed")
    args = parser.parse_args()
    mode = args.mode
    dsn = dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN") if mode != "fixed" else None
    service = MetricQueryService(dsn)
    snapshot = fixture_snapshot() if mode == "fixed" else MetadataService.local().snapshot()
    registry = CapabilityRegistry(snapshot)
    if mode == "fixed":
        reports = [evaluate(case, mode, service, registry, None, None) for case in CASES.values()]
    else:
        cases = [case for case in CASES.values() if (
            case.get("live") if mode == "live" else case["status"] in {"ok", "detail"})]
        generate = gemini_generator(dotenv_values(ROOT / ".env"), include_usage=True, schema_for_prompt=schema_for_prompt) if mode == "live" else None
        with reference_connection() as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '15s'")
            reports = [evaluate(case, mode, service, registry, pg, generate) for case in cases]
    scores = {}
    for key in reports[0]["scores"]:
        values = [item["scores"][key] for item in reports if item["scores"][key] is not None]
        scores[key] = {"passed": sum(values), "total": len(values)}
    attempts = [attempt for report in reports for attempt in report["trace"]["model_attempts"]]
    usage = {key: sum(event.get("usage", {}).get(key, 0) for event in attempts)
             for key in ("prompt", "output", "total")
             if any(key in event.get("usage", {}) for event in attempts)}
    report = {
        "mode": mode, "fixture_version": 1, "passed": sum(item["passed"] for item in reports),
        "total": len(reports), "scores": scores, "model_attempts": len(attempts), "tokens": usage,
        "latency_ms": {"mean": round(sum(item["elapsed_ms"] for item in reports) / len(reports)),
                       "max": max(item["elapsed_ms"] for item in reports)},
        "cases": reports,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
