"""Invoke the registered metric service and validate evidence once per call."""
from dataclasses import dataclass
from decimal import InvalidOperation
from time import monotonic

from .business_results import RATIO_COMPONENT_METRICS, normalize_rows, with_precision


@dataclass(frozen=True)
class MetricExecution:
    result: dict
    rows: dict | None = None
    elapsed_ms: int = 0

    @property
    def code(self):
        return self.result.get("error", {}).get("code") if self.rows is None else None

    def task_result(self, call, *, agent_call=None):
        event = dict(call, status=self.result["status"], elapsed_ms=self.elapsed_ms,
                     trace_id=self.result.get("trace_id"), sources=self.result.get("sources", []))
        if agent_call is not None:
            event["agent_call"] = agent_call
        if self.code:
            return dict(event, error_code=self.code), None
        return dict(event, row_count=len(self.rows), definition=self.result.get("definition"),
                    event_time=self.result.get("event_time")), self.rows


def metric_failure(code, result=None, elapsed_ms=0):
    return MetricExecution(dict(result or {}, status="error", error={"code": code}, results=[]),
                           elapsed_ms=elapsed_ms)


def execute_metric(query, request, *, required_sources=None):
    started = monotonic()
    result = None
    rows = None
    try:
        result = query(request)
    except Exception:
        code = "tool_exception"
    else:
        try:
            if not isinstance(result, dict):
                code = "invalid_tool_response"
            elif result.get("status") != "ok":
                error = result.get("error")
                code = (error.get("code") if isinstance(error, dict) else None) or "tool_error"
            else:
                raw_rows = result.get("results")
                matches_request = all(result.get(key) == request[key]
                                      for key in ("metric_id", "start_date", "end_date"))
                sources = result.get("sources")
                if required_sources is not None and (
                    not isinstance(sources, list) or any(not isinstance(source, str) for source in sources)
                    or set(sources) != required_sources or not result.get("trace_id")
                    or not isinstance(raw_rows, list)
                    or any(not isinstance(row, dict) or not {"bucket", "value"} <= row.keys()
                           for row in raw_rows) or not matches_request
                ):
                    code = "tool_provenance_mismatch"
                elif not matches_request:
                    code = "invalid_tool_response"
                else:
                    rows = with_precision(normalize_rows, raw_rows,
                                          monthly="month" in request.get("group_by", []),
                                          has_ratio=request["metric_id"] in RATIO_COMPONENT_METRICS)
                    code = None
        except (KeyError, TypeError, ValueError, InvalidOperation):
            code = "invalid_tool_response"
    elapsed_ms = round((monotonic() - started) * 1000)
    if code:
        return metric_failure(code, result if isinstance(result, dict) else None, elapsed_ms)
    return MetricExecution(result, rows, elapsed_ms)
