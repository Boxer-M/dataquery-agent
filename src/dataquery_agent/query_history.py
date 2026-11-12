"""Bounded and redacted audit records, distinct from model and tool traces."""
from uuid import uuid4

from .conversation_context import CONTEXT_VERSION, redact


def build_record(claim, message, answer, operation, elapsed_ms):
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
    knowledge = result.get("knowledge_result", result) if result.get("path") in {"knowledge", "hybrid"} else None
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
    knowledge_audit = None if knowledge is None else {
        "evidence_status": knowledge["evidence_status"], "citations": knowledge["citations"],
        "retrieval_strategy": knowledge.get("retrieval_trace", {}).get("strategy"),
        "embedding_version": knowledge.get("retrieval_trace", {}).get("embedding_version"),
        "retrieval_trace_id": knowledge.get("retrieval_trace", {}).get("trace_id"),
        "answer_prompt_version": knowledge.get("answer_prompt_version"),
        "metric_results": result.get("metric_results", []),
    }
    return {
        **({"knowledge": knowledge_audit} if knowledge_audit is not None else {}),
        **({"query_asset": result["query_asset"]} if strategy == "published_asset" else {}),
        "query_id": str(uuid4()), "conversation_id": str(claim["conversation_id"]),
        "message_id": str(claim["message_id"]), "trace_id": trace.get("trace_id"),
        "original_question": redact(message)[:500], "context_version": CONTEXT_VERSION,
        "context_operation": operation, "route_type": answer.get("task", "conversation"),
        "execution_strategy": strategy, "execution_status": answer["status"],
        "metric_id": metric.get("metric_id") or (request or {}).get("metric_id"),
        "analysis_id": detail.get("analysis_id") or result.get("operation"),
        "resolved_request": request or plan or payload.get("parameters") or detail.get("time_constraints"),
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
        "tool_traces": [{"trace_id": tool.get("trace_id"), "status": tool.get("status"),
                         "code": tool.get("code") or tool.get("error_code")}
                        for tool in trace.get("tools", [])],
    }


def replay_summary(record, answer):
    return {
        "status": answer["status"], "code": answer.get("code"), "query_id": record["query_id"],
        "trace_id": record["trace_id"], "result_summary": record["result_summary"],
        "answer_zh": "该消息已处理，请按查询 ID 查看历史摘要。",
    }
