"""Score public labels against actual requests, raw metric rows, and current citations."""
from dataquery_agent.rag.answer import build_evidence
from evaluate_business_planning import reference_rows

from .expansion import compare_rows
from .scoring import ratio, scores, telemetry


def normalized_request(request):
    return {"filters": {}, "group_by": []} | request


def retrieval(answer, relevant):
    if not relevant:
        return None
    ids = [row["chunk_id"] for tool in answer["trace"]["tools"] for row in tool.get("candidates", [])]
    return [relevant, ids]


def expansion_checks(case, answer, calls, history, pg, reader, owner):
    label = case["golden"]
    result = answer["result"]
    knowledge = result.get("knowledge_result", result)
    trace = knowledge.get("retrieval_trace", {})
    quotes = knowledge.get("citations", [])
    ids = (result.get("metric_resolution", {}).get("metric_ids") or [item[0]["metric_id"] for item in calls]
           or (answer.get("match") or {}).get("confirmed") or result.get("plan", {}).get("metric_ids") or [])
    status_ok = answer["status"] == label["status"] if label["status"] else None
    route = label.get("tool_path")
    observed = "metric_query" if calls else "catalog_exact" if trace.get("strategy") == "catalog_exact" else (
        "rag" if trace.get("scope") else None)
    checks = {"intent_accuracy": {"knowledge": "knowledge", "catalog_definition": "knowledge", "metric": "single",
                                  "business_plan": "business"}[label["intent"]] == answer["task"],
              "tool_route_accuracy": route == observed if route else None}
    if label["metric_ids"] is not None:
        checks["entities"] = [label["metric_ids"], ids]
    proof = label["sql"]
    if proof:
        expected = proof["calls"]
        matches_request = [normalized_request(item["parameters"]) for item in expected] == [
            normalized_request(request) for request, _ in calls
        ]
        checks["sql_result_accuracy"] = matches_request and answer["status"] == "ok" and all(
            compare_rows(item["expected_result"], actual.get("results")) is True
            for item, (_, actual) in zip(expected, calls))
        independent = [list(reference_rows(pg, item["parameters"]).values()) for item in expected]
        for rows in independent:
            rows.sort(key=lambda row: (row["bucket"] is None, row["bucket"] or ""))
        if not all(compare_rows(item["expected_result"], rows) for item, rows in zip(expected, independent)):
            raise ValueError("authored truth differs from current ODS")
        if matches_request and all(actual["status"] == "ok" for _, actual in calls):
            checks["ods_consistency"] = all(compare_rows(rows, actual["results"]) is True
                                             for rows, (_, actual) in zip(independent, calls))
        if label["plan"]:
            plan = result.get("plan") or {}
            checks["plan_validity"] = (plan.get("analysis_type") == label["plan"]["analysis_type"]
                                       and [step["request"] for step in plan.get("required_tool_calls", [])]
                                       == label["plan"]["requests"])
    if route == "catalog_exact":
        evidence = label["evidence"]
        if evidence:
            definition_is_valid = len(quotes) == 1 and all(quotes[0].get(key) == value for key, value in {
                "source_uri": evidence["source"], "document_version": evidence["version"],
                "quote": evidence["quote"], "source_type": "catalog",
            }.items())
            checks["citation_validity"] = bool(definition_is_valid and history and
                                              (history.get("knowledge") or {}).get("citations") == quotes)
        else:
            checks["guardrail"] = not quotes and not calls and trace.get("reason") == label["code"]
    if label["status"] == "unsupported":
        checks["guardrail"] = status_ok and not calls and not quotes and answer["code"] == label["code"]
    if behavior := label.get("behavior"):
        rule = behavior["review_rule"]
        if route == "rag":
            valid = reader.current_pdf_citations(quotes, owner=owner)
            if quotes:
                checks["citation_validity"] = valid == quotes
            if rule == "no_page_evidence":
                checks["guardrail"] = not quotes
            elif rule == "superseded_source":
                checks["guardrail"] = valid == quotes and not any("4B" in item["quote"] for item in quotes)
            elif rule == "coordinates_unavailable":
                checks["guardrail"] = valid == quotes and not any(
                    {"bounds", "bbox", "confidence"}.intersection(item) for item in quotes)
            else:
                checks["guardrail"] = not quotes
            # These are safety checks. A reviewer must decide whether the final answer meets the user's need.
            checks["citation_semantic_support"] = None
        else:
            checks["guardrail"] = answer["status"] in behavior["acceptable_status"] and not calls and not quotes
            checks["intent_accuracy"] = None
    checks["status_correct"] = status_ok
    checks["task_success"] = all(value for key, value in checks.items() if key != "entities" and value is not None)
    if "entities" in checks:
        checks["task_success"] &= set(checks["entities"][0]) == set(checks["entities"][1])
    return checks


def summarize(rows, excluded, k=6):
    totals = scores(rows, k=k)
    for key in ("ods_consistency", "guardrail"):
        values = [row[key] for row in rows if row.get(key) is not None]
        totals[key] = ratio(sum(values), len(values), len(rows) - len(values))
    return {"accuracy": totals, "telemetry": telemetry(rows),
            "latency_ms_by_task": {task: telemetry([r for r in rows if r["task_type"] == task])["latency_ms"]
                                   for task in sorted({r["task_type"] for r in rows})},
            "excluded": len(excluded), "executed": len(rows)}
