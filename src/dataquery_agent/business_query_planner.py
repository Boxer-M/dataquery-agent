"""Natural-language planning with validated, bounded read-only metric calls."""
import argparse
import json
from datetime import datetime
from decimal import InvalidOperation
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .business_plan import PlanError, PlanValidator, parse_proposal
from .business_results import RATIO_COMPONENT_METRICS, combine, normalize_rows, render, with_precision
from .intent_semantic_resolver import gemini_generator, outcome
from .metric_query_service import MetricQueryService
from .metrics import ROOT

QUERY_BUDGET_SECONDS = 30


class BusinessQueryPlanner:
    def __init__(self, service):
        self.service = service
        self.validator = PlanValidator(service)

    def answer(self, question, generate, *, today=None):
        trace_id = uuid4().hex
        started = monotonic()
        result = self.run(question, generate, today)
        return {**result, "trace_id": trace_id, "elapsed_ms": round((monotonic() - started) * 1000)}

    def run(self, question, generate, today):
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            return outcome("clarify", "invalid_question", "请提供一条不超过 500 字的经营分析问题。")
        try:
            raw = generate(self.validator.prompt(question))
        except Exception:
            return outcome("error", "model_unavailable", "暂时无法规划问题，请稍后重试。")
        try:
            proposal = parse_proposal(raw)
        except (ValueError, RecursionError):
            return outcome("error", "invalid_model_output", "模型未返回有效的查询计划，请重试。")
        try:
            plan = self.validator.validate(question, proposal, today or datetime.now(ZoneInfo("Asia/Shanghai")).date())
        except PlanError as error:
            return error.response
        return self._execute(plan)

    def _execute(self, plan):
        deadline = monotonic() + QUERY_BUDGET_SECONDS
        tools, successful = [], {}
        for call in plan["required_tool_calls"]:
            if monotonic() >= deadline:
                tools.append({**call, "status": "skipped", "error_code": "plan_timeout", "elapsed_ms": 0})
                continue
            trace, rows = self.call_tool(call, plan)
            tools.append(trace)
            if trace["status"] == "ok":
                key = (call["window_index"], call["request"]["metric_id"])
                successful[key] = rows
        rows, notice = with_precision(combine, plan, successful)
        status = "ok" if len(successful) == len(tools) else "partial" if successful else "error"
        return {
            "status": status, "plan": plan, "tools": tools, "results": rows, "notice": notice,
            "answer_zh": with_precision(render, plan, rows, tools, self.service.metrics, notice),
        }

    def call_tool(self, call, plan):
        request = call["request"]
        started = monotonic()
        trace = dict(call)
        try:
            result = self.service.query(request)
        except Exception:
            result = {"status": "error", "error": {"code": "tool_exception"}}
        trace.update(status="error", elapsed_ms=round((monotonic() - started) * 1000))
        if not isinstance(result, dict):
            return dict(trace, error_code="invalid_tool_response"), None
        trace["trace_id"] = result.get("trace_id")
        trace["sources"] = result.get("sources", [])
        if result.get("status") != "ok":
            error = result.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            return dict(trace, error_code=code or "tool_error"), None
        mid = request["metric_id"]
        try:
            if any(result.get(key) != request[key] for key in ("metric_id", "start_date", "end_date")):
                raise ValueError("Unexpected metric result.")
            rows = with_precision(normalize_rows, result["results"],
                                  monthly="month" in plan["group_by"], has_ratio=mid in RATIO_COMPONENT_METRICS)
        except (TypeError, KeyError, ValueError, InvalidOperation):
            return dict(trace, error_code="invalid_tool_response"), None
        return dict(trace, status="ok", row_count=len(rows), definition=result.get("definition"),
                    event_time=result.get("event_time")), rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    args = parser.parse_args()
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
    result = BusinessQueryPlanner(service).answer(args.question, gemini_generator(dotenv_values(ROOT / ".env"), retry_once=True))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
