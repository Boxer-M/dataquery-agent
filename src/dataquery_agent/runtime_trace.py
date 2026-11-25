"""Shared execution evidence projection for live and stored query responses."""

from .capability_trace import BINDING_FIELDS


def trace_view(data):
    result, trace = data.get("result", {}), data.get("trace", {})
    return {
        "run_id": data.get("query_id"),
        "trace_id": trace.get("trace_id") or data.get("trace_id"),
        "plan_id": result.get("plan_id") or data.get("plan_id"),
        "attempt_id": result.get("attempt_id") or data.get("attempt_id"),
        "subtasks": result.get("subtasks", data.get("subtasks", [])),
        "capability_records": trace.get("capability_records", data.get("capability_records", [])),
        "skills": trace.get("skills", data.get("skills", [])),
        "routing": trace.get("routing", data.get("routing", {})),
        "tool_traces": [tool_event(item) for item in trace.get("tools", data.get("tool_traces", []))],
    }


def record_routing(answer, before, after, operation):
    trace = answer["trace"]
    route = trace.setdefault("routing", {"rule": "context_" + operation})
    route.update(context_operation=operation, final_route=answer.get("task"), reason=answer.get("code"),
                 query_rewrite=operation == "knowledge_followup", model_called=bool(trace.get("model_attempts")))
    prior, current = (before or {}).get("request", {}), (after or {}).get("request", {})
    route.update(inherited={}, modified={})
    if operation in {"time", "channel", "grouping", "metric", "followup", "remove_channel"}:
        for key in ("metric_id", "start_date", "end_date", "group_by", "filters", "observation_cutoff"):
            if key in prior and prior.get(key) == current.get(key):
                route["inherited"][key] = prior[key]
            elif key in current:
                route["modified"][key] = {"before": prior.get(key), "after": current[key]}


def tool_event(item):
    fields = (*BINDING_FIELDS, "capability_trace_id", "request", "metric_id", "strategy",
              "kind", "tool", "elapsed_ms", "sources", "agent", "scope", "includes_reviewed_pdf",
              "depends_on", "failed_dependencies", "schedule_position", "cache")
    agent_call = item.get("agent_call", {})
    event = {key: item[key] for key in fields if key in item}
    if "channels" in item:
        event["retrieval"] = {key: item[key] for key in (
            "mode", "fusion", "rrf_constant", "keyword_algorithm", "channels", "returned_chunk_ids", "top_k", "vector_backend", "item_evidence", "rerank", "bm25", "rrf_order") if key in item}
    elif "retrieval" in item:
        event["retrieval"] = item["retrieval"]
    for key in ("agent", "tool", "elapsed_ms"):
        if key in agent_call:
            event.setdefault("agent_call", {})[key] = agent_call[key]
    return dict(event, trace_id=item.get("trace_id"), status=item.get("status"),
                code=item.get("code") or item.get("error_code") or item.get("reason_code"))


def record_agent_calls(trace, tasks, *, plan_id):
    trace["agent_calls"] = [
        {**task["agent_call"], "plan_id": plan_id,
         "task_id": task["task_id"], "attempt_id": task["attempt_id"]}
        for task in tasks if "agent_call" in task
    ]
