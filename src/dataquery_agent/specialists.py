"""Deterministic specialist policies with role-specific tool dispatch."""
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class ToolChoice:
    tool: str
    subject: str
    reason: str


def allowed(candidate, tool, subject):
    return isinstance(candidate, ToolChoice) and (candidate.tool, candidate.subject) == (tool, subject)


def invocation(agent, candidate, started, *, executed, model_calls=0):
    return {"agent": agent, "tool": candidate.tool if isinstance(candidate, ToolChoice) else None,
            "reason": candidate.reason if isinstance(candidate, ToolChoice) else "invalid_candidate",
            "executed": executed, "model_calls": model_calls,
            "elapsed_ms": round((monotonic() - started) * 1000, 3)}


class DataAnalysisAgent:
    def __init__(self, resolve, query_metric):
        self.resolve = resolve
        self.query_metric = query_metric

    @staticmethod
    def choose(request, route):
        if route["status"] == "STANDARD_METRIC":
            return ToolChoice("metric_query_service", request["metric_id"], "standard_metric_capability")
        return ToolChoice("none", request["metric_id"], route.get("reason_code", "unsupported_capability"))

    def execute(self, request, approved_route):
        started = monotonic()
        route = self.resolve(request)
        candidate = self.choose(request, route)
        permitted = (route["status"] == approved_route["status"] == "STANDARD_METRIC"
                     and all(route.get(key) == approved_route.get(key) for key in (
                         "required_fields", "metadata_version", "metric_request", "time_constraints", "selected_source"))
                     and allowed(candidate, "metric_query_service", request["metric_id"]))
        result = (self.query_metric(request, route) if permitted else
                  {"status": "error", "error": {"code": "data_tool_not_authorized"}})
        return dict(result, agent_call=invocation("data_analysis", candidate, started, executed=permitted))


class KnowledgeAgent:
    def __init__(self, lookup_catalog, answer_document):
        self.lookup_catalog = lookup_catalog
        self.answer_document = answer_document

    @staticmethod
    def choose(kind, subject):
        if kind == "formal_definition":
            return ToolChoice("catalog_exact", subject, "formal_metric_requires_current_catalog")
        if kind in {"business_rule", "technical_document"}:
            return ToolChoice("rag_retrieval", subject, "unstructured_knowledge_requires_retrieval")
        return ToolChoice("none", subject, "unsupported_knowledge_task")

    def execute(self, kind, subject, *, known_metrics, runtime=None):
        started = monotonic()
        candidate = self.choose(kind, subject)
        initial_calls = len(runtime.attempts) if runtime is not None else 0
        if (kind == "formal_definition" and subject in known_metrics and self.lookup_catalog is not None
                and allowed(candidate, "catalog_exact", subject)):
            result, executed = self.lookup_catalog(subject), True
        elif (kind in {"business_rule", "technical_document"} and self.answer_document is not None
              and allowed(candidate, "rag_retrieval", subject)):
            result, executed = self.answer_document(subject, kind == "business_rule"), True
        else:
            result, executed = {
                "status": "unsupported", "code": "knowledge_tool_not_authorized",
                "answer_zh": "该知识任务没有获准的工具或正式指标。",
                "evidence_status": "insufficient", "citations": [],
            }, False
        model_calls = len(runtime.attempts) - initial_calls if runtime is not None else 0
        return dict(result, agent_call=invocation("knowledge", candidate, started, executed=executed,
                                                  model_calls=model_calls))
