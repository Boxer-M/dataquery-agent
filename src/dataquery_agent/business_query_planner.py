"""Natural-language planning with validated, bounded read-only metric calls."""
import argparse
import json
from datetime import datetime
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .business_plan import PlanError, PlanValidator, parse_proposal
from .business_results import combine, render, with_precision
from .intent_semantic_resolver import gemini_generator, outcome
from .metric_query_service import MetricQueryService
from .metric_tool import execute_metric
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

    def prepare(self, question, generate, today, *, pdf_question=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            return outcome("clarify", "invalid_question", "请提供一条不超过 500 字的经营分析问题。")
        try:
            prompt = self.validator.prompt(question)
            if pdf_question is not None:
                prompt += ("\n联合任务还要在同一个 JSON 对象中添加 pdf_question，逐字复制下面的 PDF 检索需求。"
                           "其余字段只规划上面的指标问题。\nPDF 检索需求 JSON：" + json.dumps(pdf_question, ensure_ascii=False))
            raw = generate(prompt)
        except Exception:
            return outcome("error", "model_unavailable", "暂时无法规划问题，请稍后重试。")
        try:
            if pdf_question is not None:
                if not isinstance(raw, str) or len(raw) > 8192:
                    raise ValueError("invalid response")
                proposal = json.loads(raw)
                if not isinstance(proposal, dict) or proposal.pop("pdf_question", None) != pdf_question:
                    return outcome("clarify", "pdf_scope_mismatch", "请确认要引用的 PDF 主题。")
                raw = json.dumps(proposal, ensure_ascii=False)
            proposal = parse_proposal(raw)
        except (ValueError, RecursionError):
            return outcome("error", "invalid_model_output", "模型未返回有效的查询计划，请重试。")
        try:
            plan = self.validator.validate(question, proposal, today or datetime.now(ZoneInfo("Asia/Shanghai")).date())
        except PlanError as error:
            return error.response
        if pdf_question is not None:
            if len(plan["required_tool_calls"]) != 1 or plan["analysis_type"] != "summary":
                return outcome("clarify", "pdf_metric_scope", "请指定一个正式指标和一个统计窗口。")
            plan["pdf_question"] = pdf_question
        return {"status": "ready", "plan": plan}

    def execute_plan(self, plan, *, query=None, catalog=None, execution_ids=None, task_query=None, document=None, metric_handler=None):
        """Schedule the validated metric calls and optional evidence tasks."""
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
        if document is not None:
            if catalog is not None or len(tasks) != 1 or not plan.get("pdf_question"):
                return outcome("error", "invalid_task_graph", "PDF 联合计划需要一个标准指标和明确的检索主题。")
            tasks.append(Task(f"{plan_id}/2", "pdf", {"question": plan["pdf_question"]}))
            tasks.append(Task(f"{plan_id}/3", "summary", {}, tuple(task.task_id for task in tasks)))

        def default_metric_handler(call):
            execute = (lambda request: task_query(call["step"], request)) if task_query is not None else query
            return self.call_tool(call, plan, query=execute)

        handlers = {"metric": metric_handler or default_metric_handler}
        if catalog is not None:
            handlers["catalog"] = catalog
        if document is not None:
            def summarize(_, dependencies):
                (sql_event, sql_rows), (_, evidence) = dependencies
                mid = plan["metric_ids"][0]
                rows, notice = with_precision(combine, plan, {(0, mid): sql_rows})
                answer = with_precision(render, plan, rows, [sql_event], self.service.metrics, notice)
                return {"status": "ok"}, f"数据库结果\n{answer}\n\nPDF 证据\n{evidence['answer_zh']}"

            handlers.update(pdf=document, summary=summarize)
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
            elif task.task_type == "catalog":
                event = {**params, **event}
                if payload is not None:
                    knowledge.append(payload)
            subtasks.append(event)
        rows, notice = with_precision(combine, plan, successful)
        status = "ok" if len(successful) == len(tools) else "partial" if successful else "error"
        result = {
            "status": status, "plan": plan, "tools": tools, "results": rows, "notice": notice,
            "answer_zh": with_precision(render, plan, rows, tools, self.service.metrics, notice),
            "plan_id": plan_id, "attempt_id": attempt_id, "subtasks": subtasks, "catalog_results": knowledge,
        }
        if document is not None:
            result["pdf_result"] = outcomes[f"{plan_id}/2"][1]
            result["pdf_summary"] = outcomes[f"{plan_id}/3"][1]
        return result

    def call_tool(self, call, plan, *, query=None):
        execution = execute_metric(query or self.service.query, call["request"])
        return execution.task_result(call, agent_call=execution.result.get("agent_call"))


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
