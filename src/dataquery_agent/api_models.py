"""Public API schemas and explicit projections of Agent output."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from typing_extensions import Annotated

from .conversation_context import redact

Status = Literal["completed", "partial", "clarification_required", "failed"]
STATUS = {"ok": "completed", "partial": "partial", "clarify": "clarification_required"}
MAX_ROWS = 50
MAX_TEXT = 4000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    message_id: UUID
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500, strict=True)]


class ConversationResponse(BaseModel):
    conversation_id: UUID


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ApiError(BaseModel):
    code: str
    message: str


class Citation(BaseModel):
    document_id: str | None = None
    document_version: str | None = None
    chunk_id: str | None = None
    title: str | None = None
    source_uri: str | None = None
    source_location: str | None = None
    source_type: str | None = None
    quote: str | None = None


class MetricDefinition(BaseModel):
    metric_id: str | None = None
    name: str | None = None
    definition: str | None = None
    definition_version: str | None = None


class TraceStep(BaseModel):
    trace_id: str | None = None
    plan_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    capability_trace_id: str | None = None
    capability_phase: str | None = None
    metric_id: str | None = None
    agent: str | None = None
    tool: str | None = None
    kind: str | None = None
    status: str | None = None
    code: str | None = None
    sources: list[str] = Field(default_factory=list)
    depends_on: list[str] | None = None
    failed_dependencies: list[str] | None = None
    schedule_position: int | None = None
    elapsed_ms: int | float | None = None
    agent_elapsed_ms: int | float | None = None


class PublicTrace(BaseModel):
    previous_query_id: UUID | None = None
    trace_id: str | None = None
    plan_id: str | None = None
    attempt_id: str | None = None
    tasks: list[TraceStep] = Field(default_factory=list)
    capability: list[TraceStep] = Field(default_factory=list)
    tools: list[TraceStep] = Field(default_factory=list)


class QueryResponse(BaseModel):
    status: Status
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    query_id: UUID | None = None
    answer: str | None = None
    error: ApiError | None = None
    rows: list[dict[str, JsonValue]] = Field(default_factory=list)
    rows_truncated: bool = False
    row_count: int | None = None
    citations: list[Citation] = Field(default_factory=list)
    definitions: list[MetricDefinition] = Field(default_factory=list)
    clarification_choices: list[MetricDefinition] = Field(default_factory=list)
    trace: PublicTrace
    replayed: bool = False


def text(value, limit=MAX_TEXT):
    return redact(value)[:limit] if isinstance(value, str) else None


def identifier(value):
    # Preserve opaque content hashes. Free-form text always passes through redact().
    if isinstance(value, str) and len(value) <= 100 and value.isascii() and all(
            char.isalnum() or char in "_-/" for char in value):
        return value
    return None


def projection(model, item, *, ids=()):
    return model(**{key: identifier(item.get(key)) if key in ids else text(item.get(key), 600)
                    for key in model.model_fields if key in item})


def steps(items):
    identifiers = {"plan_id", "task_id", "attempt_id", "trace_id", "capability_trace_id"}
    output = []
    for item in items[:24]:
        details = item.get("agent_call", {})
        request = item.get("request") or item.get("authorization_subject") or {}
        fields = {key: identifier(item.get(key)) if key in identifiers else text(item.get(key), 120)
                  for key in TraceStep.model_fields if key not in {"sources", "depends_on", "failed_dependencies", "schedule_position", "elapsed_ms", "agent_elapsed_ms"}}
        fields.update(metric_id=text(item.get("metric_id") or request.get("metric_id"), 120),
                      agent=text(details.get("agent") or item.get("agent"), 120),
                      kind=text(item.get("kind") or ("metric" if item.get("request", {}).get("metric_id") else None), 120),
                      tool=text(details.get("tool") or item.get("tool") or item.get("strategy"), 120),
                      code=text(item.get("code") or item.get("error_code") or item.get("reason_code"), 120))
        fields["sources"] = [text(source, 150) for source in item.get("sources", [])[:10] if isinstance(source, str)]
        for key in ("depends_on", "failed_dependencies"):
            if key in item:
                fields[key] = [value for dep in item[key][:12] if (value := identifier(dep))]
        for key, value in (("schedule_position", item.get("schedule_position")),
                           ("elapsed_ms", item.get("elapsed_ms")),
                           ("agent_elapsed_ms", details.get("elapsed_ms"))):
            if type(value) in (int, float) and 0 <= value < 1e9:
                fields[key] = value
        output.append(TraceStep(**fields))
    return output


ROW_FIELDS = {"value", "bucket", "month", "start_date", "end_date", "numerator", "denominator",
              "change", "change_pct", "change_unit", "change_pct_reason", "baseline", "current"}


def metric_row(row):
    output = {}
    for key in row.keys() & ROW_FIELDS:
        value = row[key]
        if isinstance(value, dict) and key in {"baseline", "current"}:
            output[key] = metric_row(value)
        elif isinstance(value, str):
            output[key] = text(value, 150)
        elif value is None or type(value) in (int, bool):
            output[key] = value
    if isinstance(row.get("metrics"), dict):
        output["metrics"] = {
            mid: metric_row(cell) if isinstance(cell, dict) else None
            for mid, cell in list(row["metrics"].items())[:6] if identifier(mid)
        }
    return output


def public_response(data, *, history=False):
    result, trace = data.get("result", {}), data.get("trace", {})
    knowledge = (data.get("knowledge", {}) if history else result.get("knowledge_result", result))
    primary = result.get("query_result") or result
    rows = primary.get("results", []) if not history and ("query_result" in result or "plan" in result) else []
    summary = data.get("result_summary", {})
    stored_rows = data.get("recovery", {}).get("results", []) if history else []
    if history or data.get("replayed"):
        rows = stored_rows or ([summary["scalar"]] if summary.get("scalar") else [])
    tasks = result.get("subtasks", data.get("subtasks", []))
    capabilities = trace.get("capability_records", data.get("capability_records", []))
    tools = trace.get("tools", data.get("tool_traces", []))
    status = STATUS.get(data.get("execution_status" if history else "status"), "failed")
    code = identifier(data.get("error_code") if history else data.get("code"))
    error = ApiError(code=code or "request_failed", message="请求未能完成。") if status == "failed" else None
    if status == "partial" and code:
        error = ApiError(code=code, message="部分任务未能完成。")
    citations = [projection(Citation, item, ids={"document_id", "chunk_id"})
                 for item in knowledge.get("citations", [])[:10]]
    return QueryResponse(
        status=status, query_id=data.get("query_id"), conversation_id=data.get("conversation_id"),
        message_id=data.get("message_id"), answer=text(data.get("answer_zh")) if status != "failed" else None,
        error=error, rows=[metric_row(row) for row in rows[:MAX_ROWS] if isinstance(row, dict)],
        rows_truncated=bool(len(rows) > MAX_ROWS or summary.get("truncated") or primary.get("truncated")),
        row_count=summary.get("row_count", len(rows)), citations=citations,
        definitions=[projection(MetricDefinition, item) for item in
                     ([primary] if "query_result" in result else tasks)[:12] if item.get("definition")],
        clarification_choices=[projection(MetricDefinition, item) for item in data.get("choices", [])[:8]],
        trace=PublicTrace(previous_query_id=(result.get("recovery") or data.get("recovery") or {}).get("previous_query_id"),
                          trace_id=identifier(trace.get("trace_id") or data.get("trace_id")),
                          plan_id=identifier(result.get("plan_id") or data.get("plan_id")),
                          attempt_id=identifier(result.get("attempt_id") or data.get("attempt_id")),
                          tasks=steps(tasks), capability=steps(capabilities), tools=steps(tools)),
        replayed=bool(data.get("replayed")),
    )
