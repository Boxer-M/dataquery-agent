"""Bounded and redacted audit records, distinct from model and tool traces."""
from uuid import uuid4

from .conversation_context import CONTEXT_VERSION, redact
from .runtime_trace import trace_view


def export_snapshot(answer, metrics):
    """Bounded scalar summary only; retain original values/names, never re-query on export."""
    result = answer.get("result", {})
    plan, rows = result.get("plan"), result.get("results", [])
    metric = result.get("query_result", {})
    if metric.get("status") == "ok" and not metric.get("group_by") and not metric.get("truncated"):
        mid = metric.get("metric_id")
        plan = {"analysis_type": "summary", "metric_ids": [mid], "comparison_type": None, "group_by": [],
                "time_windows": [{"start_date": metric.get("start_date"), "end_date": metric.get("end_date")}]}
        rows = [{"bucket": row.get("bucket"), "month": None,
                 "metrics": {mid: {"value": row.get("value")}}} for row in metric.get("results", [])]
    if (answer.get("status") != "ok" or result.get("status") != "ok" or not isinstance(plan, dict)
            or plan.get("analysis_type") != "summary" or plan.get("group_by") or plan.get("comparison_type")
            or not isinstance(plan.get("time_windows"), list) or len(plan["time_windows"]) != 1
            or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get("bucket") != "all" or rows[0].get("month") is not None
            or result.get("truncated") or result.get("rows_truncated")):
        return None
    ids, cells = plan.get("metric_ids", []), rows[0].get("metrics", {})
    if (not isinstance(metrics, dict) or not isinstance(ids, list) or not 1 <= len(ids) <= 6
            or any(not isinstance(mid, str) for mid in ids) or len(set(ids)) != len(ids)
            or not isinstance(cells, dict) or set(ids) != set(cells)):
        return None
    names, values = {}, {}
    for mid in ids:
        metadata, cell = metrics.get(mid), cells[mid]
        name = metadata.get("name_zh") if isinstance(metadata, dict) else None
        value = cell.get("value") if isinstance(cell, dict) else None
        if not isinstance(name, str) or not 0 < len(name) <= 120 or type(value) not in (str, int) or len(str(value)) > 64:
            return None
        names[mid] = {"name_zh": name}
        values[mid] = {"value": value}
    return {"version": 1, "metrics": names, "result": {
        "status": "ok", "plan": {"analysis_type": "summary", "metric_ids": ids,
            "time_windows": plan["time_windows"], "comparison_type": None, "group_by": []},
        "results": [{"month": None, "bucket": "all", "metrics": values}],
    }}


def build_record(claim, message, answer, operation, elapsed_ms, *, query_id=None, metrics=None):
    result = answer.get("result", {})
    trace = answer.get("trace", {})
    routes = answer.get("routes", [])
    metric = result.get("query_result", {})
    detail = result.get("detail_result", {})
    payload = metric or detail or result
    execution = payload.get("execution_trace", {})
    rows = payload.get("results", [])
    sources = set(payload.get("sources", []))
    for tool in trace.get("tools", []):
        sources.update(tool.get("sources", []))
    request = routes[0].get("metric_request") if len(routes) == 1 else None
    plan = result.get("plan")
    knowledge = result.get("knowledge_result", result) if result.get("path") in {"knowledge", "hybrid", "sql_document_hybrid"} else None
    strategy = (result["path"] if knowledge is not None else
                "published_asset" if result.get("path") == "published_asset" else
                "dynamic_sql" if result.get("path") == "dynamic_sql" else
                "approved_template" if detail else "standard_plan" if plan else
                "standard_metric" if request else "no_execution")
    # Store only executed validated SQL, never a rejected proposal or model prompt.
    statement = payload.get("sql") if strategy in {"dynamic_sql", "approved_template", "published_asset"} and answer["status"] == "ok" else None
    scalar = {}
    if len(rows) == 1 and rows[0].get("bucket") == "all":
        scalar = {key: rows[0][key] for key in ("value", "numerator", "denominator") if key in rows[0]}
    if len(rows) == 1 and set(rows[0]) == {"order_count"}:
        scalar = {"order_count": rows[0]["order_count"]}
    retrieval = {} if knowledge is None else knowledge.get("retrieval_trace", {})
    resolution = result.get("metric_resolution")
    knowledge_audit = None if knowledge is None else {
        "execution_path": retrieval.get("strategy") or ("catalog_clarification" if resolution else None),
        "retrieval_scope": retrieval.get("scope"),
        "includes_reviewed_pdf": retrieval.get("includes_reviewed_pdf"),
        "query_embedding_called": retrieval.get("query_embedding_called", False if resolution else None),
        "metric_resolution": resolution,
        "reason": retrieval.get("reason") or answer.get("code"),
        "evidence_status": knowledge["evidence_status"], "citations": knowledge["citations"],
        "retrieval_strategy": knowledge.get("retrieval_trace", {}).get("strategy"),
        "embedding_version": knowledge.get("retrieval_trace", {}).get("embedding_version"),
        "retrieval_trace_id": knowledge.get("retrieval_trace", {}).get("trace_id"),
        "answer_prompt_version": knowledge.get("answer_prompt_version"),
        "metric_results": result.get("metric_results", []),
    }
    agent_calls = trace.get("agent_calls", [])
    evidence = trace_view(answer)
    evidence.pop("run_id")  # Persist the canonical query_id only.
    snapshot = export_snapshot(answer, metrics or {})
    record = {
        **({"export_snapshot": snapshot} if snapshot else {}),
        **{key: value for key, value in evidence.items()
           if value or key in {"trace_id", "tool_traces"}},
        **({"agent_calls": agent_calls} if agent_calls else {}),
        **({"hitl": answer["hitl"]} if "hitl" in answer else {}),
        **({key: trace[key] for key in ("orchestration", "interrupt_required") if key in trace}),
        **({"recovery": result["recovery"]} if "recovery" in result else {}),
        **({"knowledge": knowledge_audit} if knowledge_audit is not None else {}),
        **({"query_asset": result["query_asset"]} if strategy == "published_asset" else {}),
        "query_id": str(query_id or uuid4()), "conversation_id": str(claim["conversation_id"]),
        "message_id": str(claim["message_id"]),
        "original_question": redact(message)[:500], "context_version": CONTEXT_VERSION,
        "context_operation": operation, "route_type": answer.get("task", "conversation"),
        "execution_strategy": strategy, "execution_status": answer["status"],
        "metric_id": metric.get("metric_id") or (request or {}).get("metric_id"),
        "analysis_id": detail.get("analysis_id") or result.get("operation"),
        "resolved_request": plan or request or payload.get("parameters") or detail.get("time_constraints"),
        "template_id": detail.get("template_id"), "template_version": detail.get("template_version"),
        "sql": statement,
        "sql_asset_reference": ("sql/warehouse/dws_metrics/dws.sql#" + request["metric_id"] if request
                                else "warehouse/detail_query_contract.json#" + detail["template_id"] if detail else None),
        "parameter_summary": payload.get("parameters") or execution.get("parameters") or request,
        "prompt_versions": sorted({item["prompt_version"] for item in trace.get("model_attempts", [])
                                   if "prompt_version" in item}),
        "metadata_versions": sorted({route["metadata_version"] for route in routes if "metadata_version" in route}),
        "metric_definition_version": metric.get("definition_version"),
        "data_build_version": None,
        "source_models": sorted(sources),
        "result_summary": {"row_count": len(rows), "truncated": bool(payload.get("truncated")), "scalar": scalar},
        "latency_ms": elapsed_ms, "error_code": answer.get("code"),
    }
    from .api_models import public_response
    view = public_response(answer).model_dump(mode="json", exclude_none=True)
    record["display_snapshot"] = {key: view[key] for key in (
        "answer", "rows", "rows_truncated", "definitions", "clarification_choices") if key in view}
    return record


def replay_summary(record, answer):
    from .export_eligibility import xlsx_export
    return {
        "xlsx_export": xlsx_export(record)[0],
        **({"hitl": record["hitl"]} if "hitl" in record else {}),
        "status": answer["status"], "code": answer.get("code"), "query_id": record["query_id"],
        "trace_id": record["trace_id"], "result_summary": record["result_summary"],
        "answer_zh": "该消息已处理，请按查询 ID 查看历史摘要。",
    }
