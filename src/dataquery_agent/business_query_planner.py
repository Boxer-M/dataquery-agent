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
from .task_dag import Task, TaskGraphError, execute_tasks

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
        prepared = self.prepare(question, generate, today)
        return self.execute_plan(prepared["plan"]) if prepared["status"] == "ready" else prepared

    def prepare(self, question, generate, today):
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
        return {"status": "ready", "plan": plan}

    def execute_plan(self, plan, *, query=None, catalog=None, execution_ids=None, task_query=None):
        """Schedule the validated metric calls and optional exact Catalog lookups."""
        plan_id, attempt_id = execution_ids or (uuid4().hex, uuid4().hex)
        tasks = [Task(f"{plan_id}/{call['step']}", "metric", call) for call in plan["required_tool_calls"]]
        if catalog is not None:
            metric_tasks = tuple(tasks)
            for index, mid in enumerate(plan["metric_ids"], len(metric_tasks) + 1):
                dependencies = tuple(task.task_id for task in metric_tasks
                                     if task.parameters["request"]["metric_id"] == mid)
                if not dependencies:
                    return outcome("error", "invalid_task_graph", "目录任务缺少对应的指标查询。")
                tasks.append(Task(f"{plan_id}/{index}", "catalog", {"metric_id": mid}, dependencies))
        def metric_handler(call):
            execute = (lambda request: task_query(call["step"], request)) if task_query is not None else query
            return self.call_tool(call, plan, query=execute)

        handlers = {"metric": metric_handler}
        if catalog is not None:
            handlers["catalog"] = catalog
        try:
            outcomes = execute_tasks(tasks, handlers, attempt_id=attempt_id,
                                     deadline=monotonic() + QUERY_BUDGET_SECONDS, clock=monotonic)
        except TaskGraphError as error:
            return outcome("error", str(error), "任务计划依赖校验失败。")
        tools, subtasks, successful, knowledge = [], [], {}, []
        for task in tasks:
            event, payload = outcomes[task.task_id]
            params = task.parameters
            if task.task_type == "metric":
                request = params["request"]
                event = {**params, **event, "metric_id": request["metric_id"]}
                tools.append(event)
                if event["status"] == "ok":
                    successful[params["window_index"], request["metric_id"]] = payload
            else:
                event = {**params, **event}
                if payload is not None:
                    knowledge.append(payload)
            subtasks.append(event)
        rows, notice = with_precision(combine, plan, successful)
        status = "ok" if len(successful) == len(tools) else "partial" if successful else "error"
        return {
            "status": status, "plan": plan, "tools": tools, "results": rows, "notice": notice,
            "answer_zh": with_precision(render, plan, rows, tools, self.service.metrics, notice),
            "plan_id": plan_id, "attempt_id": attempt_id, "subtasks": subtasks, "catalog_results": knowledge,
        }

    def call_tool(self, call, plan, *, query=None):
        request = call["request"]
        started = monotonic()
        trace = dict(call)
        try:
            result = (query or self.service.query)(request)
        except Exception:
            result = {"status": "error", "error": {"code": "tool_exception"}}
        trace.update(status="error", elapsed_ms=round((monotonic() - started) * 1000))
        if not isinstance(result, dict):
            return dict(trace, error_code="invalid_tool_response"), None
        if "agent_call" in result:
            trace["agent_call"] = result["agent_call"]
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
