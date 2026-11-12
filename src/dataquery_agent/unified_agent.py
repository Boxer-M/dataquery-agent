"""One entry point for validated metric queries, plans and business clarification."""
import argparse
import json
import re
from datetime import datetime
from decimal import InvalidOperation
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from dotenv import dotenv_values

from .agent_prompts import PROMPT_VERSIONS, intent_prompt, resolution_prompt, schema_for_prompt
from .agent_runtime import AgentRuntime, ModelUnavailable
from .business_plan import DETAIL
from .business_query_planner import BusinessQueryPlanner
from .business_results import RATIO_COMPONENT_METRICS, normalize_rows, with_precision
from .capability_registry import CapabilityRegistry
from .capability_resolver import CapabilityResolver
from .detail_executor import DetailQueryExecutor
from .dynamic_requirements import recognize as recognize_dynamic
from .dynamic_sql_engine import DynamicSqlEngine
from .intent_semantic_resolver import CHANNELS, COMPLEX, GROUPS, MODIFIERS, SemanticResolver, gemini_generator, outcome, parse_intent
from .intent_time import TIME, date_window
from .metadata_service import MetadataService
from .metric_candidates import MetricCandidates
from .metric_query_service import MetricQueryService
from .metrics import ROOT

INJECTION = re.compile(
    r"忽略.{0,12}(?:指令|规则|提示)|系统提示|开发者消息|泄露.{0,8}(?:密钥|提示)|"
    r"\b(?:select\s+.+?\s+from|drop\s+table|truncate\s+table|insert\s+into)\b",
    re.I | re.S,
)
SEQUENCE = re.compile(r"(?:同(?:一|个)?)?订单.{0,20}失败.{0,10}后.{0,8}成功(?:支付)?|先.{0,8}失败.{0,10}后.{0,8}成功.{0,10}订单")
DETAIL_CHANNEL = re.compile(r"各渠道|每个渠道")
ORDER_AXIS = re.compile(r"按订单(?:ID|号)?(?:统计|分组|汇总|查看)?(?=$|[\s，。！？、,.;?]|看|查|列)")
CLOCK_HINTS = {
    "order_time": re.compile(r"下单的|下单时间|下单日期|下单日"),
    "payment_attempt_time": re.compile(r"支付时间|支付日期|支付日"),
    "return_record_time": re.compile(r"退货时间|退货日期|退货日"),
}
TIME_TAIL = re.compile(r"季度|半年|截至|以来|之前|之后|以前|以后|[0-9]+\s*(?:月|日|天|周|点)")


class UnifiedAgent:
    def __init__(self, service, capability, detail_executor=None, dynamic_engine=None):
        self.service = service
        self.capability = capability
        self.detail_executor = detail_executor
        self.dynamic_engine = dynamic_engine
        self.semantic = SemanticResolver(service)
        self.candidates = MetricCandidates(self.semantic)
        self.planner = BusinessQueryPlanner(service)

    def answer(self, question, generate, *, today=None, state=None):
        """Answer one question. State is reserved for a future validated context."""
        started = monotonic()
        runtime = AgentRuntime(generate)
        trace = {"trace_id": uuid4().hex, "model_attempts": runtime.attempts, "tools": []}
        frame = {"task": "unknown", "routes": [], "match": None, "event_time_hint": None}
        try:
            result = self._answer(question, runtime, frame, trace,
                                  today or datetime.now(ZoneInfo("Asia/Shanghai")).date(), state)
        except ModelUnavailable:
            result = outcome("error", "model_unavailable", "模型暂时不可用，请稍后重试。")
        trace["elapsed_ms"] = round((monotonic() - started) * 1000)
        return {
            **frame, "status": result["status"], "code": result.get("code"),
            "answer_zh": result.get("answer_zh") or result.get("message") or result.get("reason") or "",
            "choices": result.get("choices", []), "result": result, "trace": trace,
        }

    def _answer(self, question, runtime, frame, trace, today, state):
        if state is not None:
            return outcome("clarify", "state_not_supported", "请在当前问题中完整说明查询需求。")
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            return outcome("clarify", "invalid_question", "请提供不超过 500 字的电商问题。")
        if INJECTION.search(question):
            frame["task"] = "blocked"
            return outcome("unsupported", "unsafe_request", "请直接描述业务需求。")
        mentions = list(dict.fromkeys(match.group() for match in self.semantic.metric_pattern.finditer(question)))
        analysis = self.recognize_analysis(question, mentions)
        if analysis in {"refund_settled_at", "browse_conversion_rate", "payment_amount_by_category"}:
            frame["task"] = "unsupported"
            route = self.check_route({"analysis_id": analysis}, frame)
            return self.route_outcome(route)
        if re.search(r"利润|净收入|毛利|到账", question):
            frame["task"] = "unsupported"
            return outcome("unsupported", "missing_financial_definition", "当前数据不足以验证这个财务口径。")
        for phrase in mentions:
            ids = self.semantic.names[phrase]
            if len(ids) > 1:
                frame.update(task="clarification", match=self.match("exact", phrase, ids))
                return self.semantic.clarify_metric(ids)
        if re.search(r"收入|经营表现", self.semantic.metric_pattern.sub("", question)):
            frame["task"] = "clarification"
            return self.semantic.clarify_metric(("item_gross_sales_amount", "item_net_sales_amount",
                                                "captured_payment_amount"))
        if not analysis and self.dynamic_engine is not None:
            requirement = recognize_dynamic(question, today)
            if requirement is not None:
                frame["task"] = "dynamic"
                result = self.dynamic_engine.execute(question, requirement, runtime, agent_trace_id=trace["trace_id"])
                execution = result["execution_trace"]
                if execution.get("capability"):
                    frame["routes"].append(execution["capability"])
                trace["tools"].append(execution)
                if result["status"] == "ok":
                    text = f"{requirement.start} 至 {requirement.end}（不含结束日），{result['definition']}"
                    text += f"返回 {len(result['results'])} 行。" if result["results"] else "无符合条件的分组。"
                    if result["results"]:
                        first = result["results"][0]
                        text += "首行：" + "，".join(f"{name}={value}" for name, value in first.items()) + "。"
                    if result["truncated"]:
                        text += "仅展示排序后的前 100 行。"
                    result["answer_zh"] = text
                return result
        if not analysis and DETAIL.search(self.semantic.metric_pattern.sub("", question)):
            frame["task"] = "detail"
            return outcome("unsupported", "detail_not_registered", "该逐笔或订单明细需求暂无已登记路径。")
        ids = list(dict.fromkeys(mid for phrase in mentions for mid in self.semantic.names[phrase]))
        clock_text = self.semantic.metric_pattern.sub("", question)
        clocks = [clock for clock, pattern in CLOCK_HINTS.items() if pattern.search(clock_text)]
        if len(clocks) > 1:
            frame["task"] = "clarification"
            return outcome("clarify", "event_time_conflict", "请为本次分析明确一种业务事件时间。")
        frame["event_time_hint"] = clocks[0] if clocks else None
        if ids:
            frame["match"] = self.match("exact", mentions, ids, confirmed=ids)
        if analysis:
            frame["task"] = "detail"
            allowed = {"item_net_sales_amount"} if analysis == "sales_by_order" else set()
            if set(ids) - allowed:
                return outcome("unsupported", "mixed_detail_scope", "请单独描述明细条件与标准指标需求。")
        elif len(ids) > 1 or COMPLEX.search(question) or len(list(TIME.finditer(question))) > 1:
            frame["task"] = "business"
            return self.business(question, runtime, frame, trace, today)
        else:
            frame["task"] = "single"

        predicate = SEQUENCE.search(question) if analysis == "payment_failure_then_capture" else None
        try:
            raw = runtime.call("intent", intent_prompt(question, mentions, predicate.group() if predicate else None))
            intent = self.parse_detail_intent(raw, question, analysis) if analysis else parse_intent(raw, question)
        except (ValueError, TypeError, RecursionError):
            runtime.invalid_output()
            return outcome("error", "invalid_model_output", "模型提取的原文片段不符合约定。")
        intent = self.grounded_channel_filters(question, intent)
        intent["groups"] = self.grounded_groups(question, intent["groups"])
        if predicate:
            intent["filters"] = [
                phrase for phrase in intent["filters"] if not (
                    phrase in predicate.group()
                    and (phrase == predicate.group() or re.fullmatch(
                        r"先(?:支付)?失败(?:[，,、\s]*(?:后来|然后|后|再)(?:支付)?成功(?:支付)?)?"
                        r"|(?:后来|然后|后|再)(?:支付)?成功(?:支付)?", phrase))
                )
            ]
        if analysis:
            return self.detail(question, intent, analysis, frame, trace, today)
        if intent["type"] == "complex":
            frame["task"] = "business"
            return self.business(question, runtime, frame, trace, today)
        binding = None
        if not ids and len(intent["metrics"]) == 1:
            evidence = intent["metrics"][0]
            if not self.candidates.anchored(evidence):
                clauses = re.split(TIME.pattern + r"|[，。；？,;?]", question)
                grounded = [clause.strip() for clause in clauses
                            if evidence in clause and self.candidates.anchored(clause)]
                if len(grounded) == 1:
                    evidence = grounded[0]
            shortlist = self.candidates.shortlist(evidence)
            frame["match"] = self.match("inferred", evidence, [item["metric_id"] for item in shortlist])
            frame["match"]["extracted_fragment"] = intent["metrics"][0]
            if shortlist:
                raw = runtime.call("resolution", resolution_prompt(question, evidence, shortlist))
                try:
                    proposed, binding = self.candidates.validate(raw, evidence, shortlist)
                except (ValueError, RecursionError):
                    runtime.invalid_output()
                    return outcome("error", "invalid_metric_proposal", "指标候选与原文或正式目录不一致。")
                frame["match"]["model_proposal"] = proposed
                if binding and self.candidates.anchored(question) != {binding.metric_id}:
                    binding = None
                if binding:
                    frame["match"]["confirmed"] = [binding.metric_id]
            if not binding:
                choices = [item["metric_id"] for item in shortlist][:3]
                result = self.semantic.clarify_metric(choices)
                return dict(result, code="unconfirmed_metric")
        resolved = self.semantic.resolve(question, intent, today, binding=binding)
        if resolved["status"] != "ready":
            return resolved
        return self.run_metric_request(resolved["request"], frame, trace)

    def run_metric_request(self, request, frame, trace):
        route = self.check_route(request, frame)
        if route["status"] != "STANDARD_METRIC":
            return self.route_outcome(route)
        result = self.query(request, route, trace)
        if result["status"] != "ok":
            return outcome("error", result["error"]["code"], "指标工具查询失败。")
        values = "、".join(f"{row['bucket']}={row['value'] if row['value'] is not None else '无结果'}" for row in result["results"][:8])
        name = self.service.metrics[request["metric_id"]]["name_zh"]
        return {
            "status": "ok", "query_result": result,
            "answer_zh": (f"{request['start_date']} 至 {request['end_date']}（不含结束日），"
                          f"{name}：{values or '无分组结果'}。{self.service.metrics[request['metric_id']]['definition']}"),
        }

    @staticmethod
    def parse_detail_intent(raw, question, analysis):
        # The registered sequence already fixes the measure. Gemini may label it
        # "订单数"; discard only that unused label, never a window or filter.
        if analysis == "payment_failure_then_capture" and isinstance(raw, str) and len(raw) <= 8192:
            intent = json.loads(raw)
            if isinstance(intent, dict) and intent.get("metrics") == ["订单数"] and "订单数" not in question:
                intent["metrics"] = []
                raw = json.dumps(intent, ensure_ascii=False)
        return parse_intent(raw, question)

    def grounded_channel_filters(self, question, intent):
        def channel_phrase(phrase):
            if not phrase.endswith("渠道") or phrase not in question:
                return None
            token = phrase[:-2]
            if token.lower() not in CHANNELS or re.search(r"(?:按|分)\s*" + re.escape(phrase), question):
                return None
            return token

        groups, filters = [], [channel_phrase(phrase) or phrase for phrase in intent["filters"]]
        for phrase in intent["groups"]:
            token = channel_phrase(phrase)
            if token:
                filters.append(token)
            else:
                groups.append(phrase)
        return dict(intent, groups=groups, filters=list(dict.fromkeys(filters)))

    def grounded_groups(self, question, groups):
        detected = [match.group() for match in self.semantic.group_pattern.finditer(question)]
        return [next((match for match in detected if match.endswith(phrase)), phrase)
                if phrase in GROUPS else phrase for phrase in groups]

    @staticmethod
    def match(method, evidence, candidates, confirmed=None):
        return {"method": method, "original_evidence": evidence, "candidate_ids": list(candidates),
                "model_proposal": [], "confirmed": confirmed or []}

    @staticmethod
    def recognize_analysis(question, mentions):
        if re.search(r"退款.{0,6}到账|退回银行卡", question):
            return "refund_settled_at"
        if re.search(r"浏览.{0,8}转化", question):
            return "browse_conversion_rate"
        if re.search(r"(?:按|分)(?:商品)?品类", question) and "支付金额" in question:
            return "payment_amount_by_category"
        if SEQUENCE.search(question):
            return "payment_failure_then_capture"
        if ORDER_AXIS.search(question) and any(
            phrase in {"商品净额", "折扣后商品销售金额", "item_net_sales_amount"} for phrase in mentions
        ):
            return "sales_by_order"
        return None

    def detail(self, question, intent, analysis, frame, trace, today):
        periods = [match.group() for match in TIME.finditer(question)]
        if len(periods) != 1 or intent["time"] != periods[0] or TIME_TAIL.search(TIME.sub("", question)):
            return outcome("clarify", "missing_time", "请选择一个完整的日期窗口。")
        try:
            start, end = date_window(periods[0], today)
        except (ValueError, OverflowError):
            return outcome("clarify", "invalid_time", "请提供有效日期。")
        if intent["missing"]:
            return outcome("clarify", "incomplete_intent", "请明确明细业务条件。")
        if MODIFIERS.search(question) or re.search(r"失败原因|筛选|过滤|支付方式为", question):
            return outcome("unsupported", "unsupported_filter", "该明细契约未登记额外状态条件。")
        grouped_question = ORDER_AXIS.sub("", question) if analysis == "sales_by_order" else question
        phrases = [phrase for phrase in intent["groups"] if not (
            analysis == "sales_by_order" and (ORDER_AXIS.fullmatch(phrase) or phrase == "订单"))]
        if DETAIL_CHANNEL.search(grouped_question):
            grouped_question = DETAIL_CHANNEL.sub("按渠道", grouped_question)
            phrases = ["按渠道" if DETAIL_CHANNEL.fullmatch(phrase) or phrase == "渠道" else phrase
                       for phrase in phrases]
        groups = self.semantic.resolve_groups(grouped_question, phrases)
        filters = self.semantic.resolve_filters(question, intent["filters"])
        if groups is None or filters is None:
            return outcome("unsupported", "unsupported_detail_scope", "明细分组或过滤与已批准路径不一致。")
        if analysis == "sales_by_order":
            groups.insert(0, "order_id")
        parameters = {"start_date": start.isoformat(), "end_date": end.isoformat(), "group_by": groups}
        route = self.check_route({"analysis_id": analysis, **parameters, "filters": filters}, frame)
        if (route["status"] != "DETAIL_QUERY" or self.detail_executor is None
                or not self.detail_executor.supports(analysis, groups)):
            return self.route_outcome(route)
        result = self.detail_executor.execute_detail(analysis, parameters, route, trace)
        trace["tools"].append(result["execution_trace"])
        if result["status"] != "ok":
            return result
        if analysis == "payment_failure_then_capture":
            count = sum(row["order_count"] for row in result["results"])
            label = "已展示渠道合计" if result["truncated"] else "符合条件的订单共"
            summary = f"{label} {count} 笔。"
            if groups and result["results"]:
                summary += "按渠道：" + "、".join(
                    f"{row['channel']} {row['order_count']} 笔" for row in result["results"][:8]) + "。"
        else:
            summary = f"返回 {len(result['results'])} 行订单商品净额。"
            if result["results"]:
                first = result["results"][0]
                summary += f"首行订单 {first['order_id']} 的该分组净额为 {first['net_amount']}。"
        if result["truncated"]:
            summary += "结果已达到行数上限，当前仅展示排序后的前 100 行。"
        return {"status": "ok", "detail_result": result,
                "answer_zh": f"{start} 至 {end}（不含结束日），{result['definition']}。{summary}"}

    def business(self, question, runtime, frame, trace, today):
        prepared = self.planner.prepare(
            question, lambda prompt: runtime.call("planning", f"[{PROMPT_VERSIONS['planning']}] {prompt}"), today)
        if prepared["status"] != "ready":
            if prepared.get("code") == "invalid_model_output":
                runtime.invalid_output()
            return prepared
        return self.run_business_plan(prepared["plan"], frame, trace)

    def run_business_plan(self, plan, frame, trace):
        routes = {}
        for call in plan["required_tool_calls"]:
            request = call["request"]
            route = self.check_route(request, frame)
            if route["status"] != "STANDARD_METRIC":
                return self.route_outcome(route)
            routes[self.request_key(request)] = route
        result = self.planner.execute_plan(
            plan, query=lambda request: self.query(request, routes[self.request_key(request)], trace))
        if result["status"] == "error":
            result["code"] = "tool_execution_failed"
        return result

    @staticmethod
    def request_key(request):
        return json.dumps(request, sort_keys=True, ensure_ascii=False)

    def check_route(self, request, frame):
        try:
            demand = dict(request)
            if frame["event_time_hint"]:
                demand["event_time"] = frame["event_time_hint"]
            route = self.capability.resolve(demand)
        except (ValueError, KeyError, psycopg.Error):
            route = {"status": "UNSUPPORTED", "reason_code": "capability_unavailable",
                     "reason": "能力验证暂时不可用。"}
        frame["routes"].append(route)
        return route

    @staticmethod
    def route_outcome(route):
        status = {"DETAIL_QUERY": "detail", "NEEDS_CLARIFICATION": "clarify",
                  "UNSUPPORTED": "unsupported"}.get(route["status"], "error")
        return outcome(status, route.get("reason_code"), route.get("reason", "无法执行此业务路径。"),
                       capability=route)

    def query(self, request, route, trace):
        started = monotonic()
        result = None
        error = None
        try:
            result = self.service.query(request)
            if not isinstance(result, dict):
                error = "invalid_tool_response"
            elif result.get("status") != "ok":
                detail = result.get("error")
                error = (detail.get("code") if isinstance(detail, dict) else None) or "tool_error"
            elif (not isinstance(result.get("sources"), list)
                  or any(not isinstance(source, str) for source in result["sources"])
                  or set(result["sources"]) != route["required_fields"].keys()
                  or not result.get("trace_id")
                  or not isinstance(result.get("results"), list)
                  or any(not isinstance(row, dict) or not {"bucket", "value"} <= row.keys()
                         for row in result["results"])
                  or any(result.get(key) != request[key] for key in ("metric_id", "start_date", "end_date"))):
                error = "tool_provenance_mismatch"
            else:
                with_precision(normalize_rows, result["results"],
                               monthly="month" in request.get("group_by", []),
                               has_ratio=request["metric_id"] in RATIO_COMPONENT_METRICS)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            error = "invalid_tool_response"
        except Exception:
            error = "tool_exception"
        result = result if isinstance(result, dict) else {}
        trace["tools"].append({
            "request": request, "status": "error" if error else "ok", "code": error,
            "trace_id": result.get("trace_id"), "sources": result.get("sources", []),
            "capability_trace_id": route["trace_id"], "elapsed_ms": round((monotonic() - started) * 1000),
        })
        return dict(result, status="error", error={"code": error}, results=[]) if error else result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    args = parser.parse_args()
    try:
        service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
        capability = CapabilityResolver(CapabilityRegistry(MetadataService.local().snapshot()))
        generate = gemini_generator(dotenv_values(ROOT / ".env"), include_usage=True, schema_for_prompt=schema_for_prompt)
        result = UnifiedAgent(service, capability, DetailQueryExecutor.local(), DynamicSqlEngine.local()).answer(args.question, generate)
    except (OSError, KeyError, ValueError, psycopg.Error):
        message = "无法初始化指标、模型或只读元数据。"
        result = {
            "status": "error", "code": "agent_initialization_failed", "answer_zh": message,
            "task": "unknown", "routes": [], "match": None, "event_time_hint": None, "choices": [],
            "result": outcome("error", "agent_initialization_failed", message),
            "trace": {"trace_id": uuid4().hex, "model_attempts": [], "tools": []},
        }
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
