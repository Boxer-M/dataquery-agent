"""One entry point for validated metric queries, plans and business clarification."""
import argparse
import json
import re
from datetime import datetime
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from dotenv import dotenv_values

from .agent_prompts import PROMPT_VERSIONS, business_planning_call, intent_prompt, resolution_prompt, schema_for_prompt
from .agent_runtime import AgentRuntime, ModelUnavailable
from .business_plan import DETAIL
from .business_query_planner import BusinessQueryPlanner
from .capability_registry import CapabilityRegistry
from .capability_resolver import CapabilityResolver
from .capability_trace import BINDING_FIELDS, capability_link
from .detail_executor import DetailQueryExecutor
from .dynamic_requirements import recognize as recognize_dynamic
from .dynamic_sql_engine import DynamicSqlEngine
from .intent_semantic_resolver import CHANNELS, COMPLEX, GROUPS, MODIFIERS, SemanticResolver, gemini_generator, outcome, parse_intent
from .intent_time import TIME, date_window
from .metadata_service import MetadataService
from .metric_candidates import MetricCandidates
from .metric_query_service import MetricQueryService
from .metric_tool import execute_metric, metric_failure
from .metrics import ROOT
from .performance import CURRENT, timed
from .runtime_trace import record_agent_calls
from .skill_registry import SkillError

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
    def __init__(self, service, capability, detail_executor=None, dynamic_engine=None, asset_runtime=None, knowledge=None, *, supervisor=False):
        self.service = service
        self.capability = capability
        self.detail_executor = detail_executor
        self.dynamic_engine = dynamic_engine
        self.asset_runtime = asset_runtime
        self.knowledge = knowledge
        self.supervisor = supervisor
        self.semantic = SemanticResolver(service)
        self.candidates = MetricCandidates(self.semantic)
        self.planner = BusinessQueryPlanner(service)

    def answer(self, question, generate, *, today=None, state=None, knowledge_owner=None):
        """Answer one question. State is reserved for a future validated context."""
        started = monotonic()
        runtime = AgentRuntime(generate)
        trace = {"trace_id": uuid4().hex, "model_attempts": runtime.attempts, "tools": []}
        frame = {"task": "unknown", "routes": [], "match": None, "event_time_hint": None}
        try:
            result = self._answer(question, runtime, frame, trace,
                                  today or datetime.now(ZoneInfo("Asia/Shanghai")).date(), state, knowledge_owner)
        except ModelUnavailable:
            result = outcome("error", "model_unavailable", "模型暂时不可用，请稍后重试。")
        trace["elapsed_ms"] = round((monotonic() - started) * 1000)
        trace.setdefault("routing", {"rule": "validated_model_intent"})
        trace["routing"].update(final_route=frame["task"], reason=result.get("code"),
                                model_called=bool(runtime.attempts), model_stages=[e["stage"] for e in runtime.attempts])
        return {
            **frame, "status": result["status"], "code": result.get("code"),
            "answer_zh": result.get("answer_zh") or result.get("message") or result.get("reason") or "",
            "choices": result.get("choices", []), "result": result, "trace": trace,
        }

    def _answer(self, question, runtime, frame, trace, today, state, knowledge_owner=None):
        if state is not None:
            return outcome("clarify", "state_not_supported", "请在当前问题中完整说明查询需求。")
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            return outcome("clarify", "invalid_question", "请提供不超过 500 字的电商问题。")
        if INJECTION.search(question):
            trace["routing"] = {"rule": "input_safety"}
            frame["task"] = "blocked"
            return outcome("unsupported", "unsafe_request", "请直接描述业务需求。")
        # Relative observation horizons require clarification before knowledge or SQL dispatch.
        if (re.search(r"(?:下单|购买)后\s*\d+\s*天", question)
                and re.search(r"商品明细.{0,28}退货率", question)
                and not re.search(r"PDF|(?:正式|业务)定义|利润|净收入|毛利|到账", question, re.I)
                and not self.recognize_analysis(question, [])):
            mid = "item_return_rate"
            if mid in self.service.metrics:
                frame.update(task="clarification", match=self.match("approved_alias", question, [mid], confirmed=[mid]))
                return outcome("clarify", "unsupported_observation_window", "已识别商品明细退货率。请确认统计窗口和受支持的观察截止日期。")
        if self.knowledge is not None:
            from .rag.routing import route_knowledge
            routed = route_knowledge(self, question, runtime, frame, trace, today, knowledge_owner)
            if routed is not None:
                return routed
        if re.search(r"(?:退款|退货).*(?:最多|最高|排行|排名)", question) and not re.search(r"金额|笔数|记录数|条数|次数|退货率|退款率|利润|净收入|毛利|到账", question):
            trace["routing"] = {"rule": "return_measure_conflict"}
            frame["task"] = "clarification"
            clarified = self.semantic.clarify_metric(("return_record_count", "return_record_amount"))
            return dict(clarified, message="要比较退货记录数还是退货记录金额？当前不支持实际退款到账口径。")
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
                result = (self.asset_runtime.execute(question, requirement, agent_trace_id=trace["trace_id"])
                          if self.asset_runtime is not None else None)
                if result is None:
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
            trace["routing"] = {"rule": "complex_business_proposal"}
            frame["task"] = "business"
            return self.business(question, runtime, frame, trace, today)
        else:
            frame["task"] = "single"

        if not analysis and len(ids) == 1 and re.search(r"查询|统计|查看|是多少", question):
            periods = list(TIME.finditer(question))
            groups = [m.group() for m in self.semantic.group_pattern.finditer(question)]
            channels = [m.group() for m in self.semantic.channel_pattern.finditer(question)]
            remainder = question
            for fragment in [*mentions, *groups, *channels, *(m.group() for m in periods)]:
                remainder = remainder.replace(fragment, "")
            if len(periods) == 1 and re.fullmatch(r"(?:请|查询|统计|查看|是多少|的|渠道|[\s，,。？?])*", remainder):
                trace["routing"] = {"rule": "exact_metric_explicit_window"}
                resolved = self.semantic.resolve(question, {"metrics": mentions, "time": periods[0].group(),
                    "groups": groups, "filters": channels, "type": "metric", "missing": []}, today)
                return self.run_metric_request(resolved["request"], frame, trace) if resolved["status"] == "ready" else resolved

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
        from .preference_memory import channel_request
        request = channel_request(request, self.service, trace)
        plan_id, attempt_id = uuid4().hex, uuid4().hex
        task = {"plan_id": plan_id, "task_id": f"{plan_id}/1", "attempt_id": attempt_id,
                "kind": "metric", "metric_id": request["metric_id"], "depends_on": [],
                "schedule_position": 1}
        binding = {key: task[key] for key in ("plan_id", "task_id", "attempt_id")}
        binding.update(authorization_subject=dict(request), capability_phase="preflight")
        route = self.bind_metric(request, frame, trace, binding)
        identity = {"plan_id": plan_id, "attempt_id": attempt_id, "subtasks": [task]}
        if route["status"] != "STANDARD_METRIC":
            task.update(status="skipped", code=route.get("reason_code"))
            return {**self.route_outcome(route), **identity}
        result = self.query(request, route, trace, binding=binding)
        task.update(status=result["status"], trace_id=result.get("trace_id"),
                    capability_trace_id=route.get("trace_id"), sources=result.get("sources", []))
        if result["status"] != "ok":
            code = result["error"]["code"]
            task["code"] = code
            return {**outcome("error", code, "指标工具查询失败。"), **identity}
        values = "、".join(f"{row['bucket']}={row['value'] if row['value'] is not None else '无结果'}" for row in result["results"][:8])
        name = self.service.metrics[request["metric_id"]]["name_zh"]
        return {
            **identity, "status": "ok", "query_result": result,
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

    def business(self, question, runtime, frame, trace, today, *, catalog=None, owner=None, pdf_question=None):
        try:
            generate = business_planning_call(question, runtime, trace)
        except SkillError:
            return outcome("error", "skill_unavailable", "经营分析指导暂不可用，本次未执行查询。")
        prepared = self.planner.prepare(
            question, generate, today,
            pdf_question=pdf_question)
        trace.setdefault("routing", {"rule": "business_proposal"})["plan_validation"] = {
            "status": prepared["status"], "code": prepared.get("code")}
        if "date_window_validation" in prepared:
            trace["routing"]["date_window_validation"] = prepared["date_window_validation"]
        if prepared["status"] != "ready":
            if prepared.get("code") == "invalid_model_output":
                runtime.invalid_output()
            if pdf_question is not None and prepared.get("choices"):
                names = "、".join(choice["name"] for choice in prepared["choices"])
                return outcome("clarify", prepared["code"], f"请在原问题中明确指标口径（{names}），并保留 PDF 查询需求。")
            return prepared
        plan = prepared["plan"]
        if self.supervisor and catalog is None:
            from .supervisor_graph import role_context, run_supervisor

            contexts = {"data": role_context(question, metric_ids=plan["metric_ids"],
                                             analysis_type=plan["analysis_type"])}
            if pdf_question is not None:
                from .rag.routing import run_document_plan

                contexts["knowledge_agent"] = role_context(pdf_question, source="reviewed_pdf")
                return run_supervisor(runtime, trace, contexts=contexts, execute=lambda roles:
                    run_document_plan(self, plan, runtime, frame, trace, today, owner, roles=roles))
            return run_supervisor(runtime, trace, contexts=contexts, execute=lambda roles:
                self.run_business_plan(plan, frame, trace, roles=roles))
        if pdf_question is not None:
            from .rag.routing import run_document_plan
            return run_document_plan(self, plan, runtime, frame, trace, today, owner)
        if catalog is None:
            return self.run_business_plan(plan, frame, trace)
        return self.run_hybrid_plan(plan, catalog, frame, trace, today, owner)

    def run_hybrid_plan(self, plan, resolution, frame, trace, today, owner, *, keep_metric_results=False):
        from .catalog_recovery import checkpoint_partial
        from .rag.routing import catalog_tasks
        from .specialists import KnowledgeAgent

        plan["include_catalog_definitions"] = True
        resolution = dict(resolution, method=resolution.get("method", "validated_plan"),
                          metric_ids=plan["metric_ids"])

        specialist = KnowledgeAgent(
            lookup_catalog=lambda mid: self.knowledge.exact(self.service, dict(resolution, metric_ids=[mid]),
                                                            today=today, owner=owner),
            answer_document=None,
        )

        def catalog(params):
            mid = params["metric_id"]
            evidence = specialist.execute("formal_definition", mid, known_metrics=self.service.metrics)
            if "retrieval_trace" not in evidence:
                return {"status": "unavailable", "error_code": evidence["code"], "sources": [],
                        "agent_call": evidence["agent_call"]}, evidence
            trace["tools"].append(evidence["retrieval_trace"])
            task = catalog_tasks(evidence, [mid])[0]
            return dict(task, agent_call=evidence["agent_call"]), evidence

        proof, metric_results = {}, []
        result = self.run_business_plan(plan, frame, trace, catalog=catalog, recovery_proof=proof if owner else None,
                                        metric_results=metric_results if keep_metric_results else None)
        if "tools" not in result:
            return result
        subtasks = result["subtasks"]
        record_agent_calls(trace, subtasks, plan_id=result["plan_id"])
        catalogs = [task for task in subtasks if task["kind"] == "catalog"]
        citations = [task["citation"] for task in catalogs if task["status"] == "ok"]
        evidence_status = ("supported" if len(citations) == len(catalogs)
                           else "partial" if citations else "insufficient")
        evidence_results = result.pop("catalog_results")
        sections = []
        citation_number = 0
        for evidence in evidence_results:
            section = evidence["answer_zh"]
            if evidence["citations"]:
                citation_number += 1
                section = section.replace(" [1] ", f" [{citation_number}] ", 1)
            sections.append(section)
        if any(task["status"] == "skipped" for task in catalogs):
            sections.append("部分正式定义因关联查询失败或计划超时而跳过。")
        retrieval = {
            "trace_id": uuid4().hex, "strategy": "catalog_exact", "query_embedding_called": False,
            "metric_ids": plan["metric_ids"],
            "candidates": citations,
            "metric_errors": {task["metric_id"]: task["error_code"] for task in catalogs if task["status"] != "ok"},
        }
        trace["tools"].append(retrieval)
        knowledge = {"status": "ok", "citations": citations, "evidence_status": evidence_status,
                     "answer_zh": "\n".join(sections), "retrieval_trace": retrieval,
                     "uncited_metric_ids": [task["metric_id"] for task in catalogs if task["status"] != "ok"]}
        status = result["status"]
        if status == "ok" and evidence_status != "supported":
            status = "partial"
            saved = checkpoint_partial(proof.get("checkpoint"), results=result["results"],
                                       sql_answer=result["answer_zh"], metric_results=metric_results,
                                       plan_id=result["plan_id"], attempt_id=result["attempt_id"])
            if saved:
                result["recovery"] = saved
        return dict(result, status=status, path="hybrid", knowledge_result=knowledge, subtasks=subtasks,
                    metric_resolution=resolution, metric_results=metric_results,
                    answer_zh=result["answer_zh"] + "\n正式定义\n" + knowledge["answer_zh"])

    def run_business_plan(self, plan, frame, trace, *, recovery_proof=None, catalog=None, metric_results=None, document=None, roles=None):
        from .preference_memory import channel_plan
        plan = channel_plan(plan, self.service, trace)
        plan_id, attempt_id = uuid4().hex, uuid4().hex
        routes = {}
        def resolve(request, step, phase):
            return self.bind_metric(request, frame, trace, {
                "plan_id": plan_id, "task_id": f"{plan_id}/{step}", "attempt_id": attempt_id,
                "authorization_subject": dict(request), "capability_phase": phase,
            })

        for call in plan["required_tool_calls"]:
            route = resolve(call["request"], call["step"], "preflight")
            if route["status"] != "STANDARD_METRIC":
                return self.route_outcome(route)
            routes[call["step"]] = route
        if recovery_proof is not None:
            from .catalog_recovery import sql_checkpoint
            before = sql_checkpoint(self.service, plan, list(routes.values()), capture_snapshot=True)

        def run_metric(call):
            step, request = call["step"], call["request"]
            approved = routes[step]
            agent_call = None

            def execute(request, route):
                phase = "execution_recheck" if catalog is not None or document is not None or roles is not None else "preflight"
                binding = {key: approved[key] for key in BINDING_FIELDS} | {"capability_phase": phase}
                return self.execute_bound_metric(request, route, trace, binding=binding)

            if roles is not None and "data" not in roles:
                return metric_failure("data_delegation_declined").task_result(call)
            if catalog is not None or document is not None or roles is not None:
                from .specialists import DataAnalysisAgent
                specialist = DataAnalysisAgent(
                    resolve=lambda request: resolve(request, step, "execution_recheck"),
                    query_metric=execute,
                )
                execution, agent_call = specialist.dispatch(request, approved)
                if not agent_call["executed"]:
                    execution = metric_failure("data_tool_not_authorized")
            else:
                execution = execute(request, approved)
            if metric_results is not None and execution.result["status"] == "ok":
                metric_results.append(execution.result if agent_call is None else
                                      dict(execution.result, agent_call=agent_call))
            return execution.task_result(call, agent_call=agent_call)

        result = self.planner.execute_plan(plan, metric_handler=run_metric, catalog=catalog, document=document,
                                           execution_ids=(plan_id, attempt_id))
        if recovery_proof is not None and before and result["status"] == "ok":
            if sql_checkpoint(self.service, plan, list(routes.values()), capture_snapshot=True) == before:
                recovery_proof["checkpoint"] = {key: value for key, value in before.items() if key != "snapshot"}
        if result["status"] == "error":
            result.setdefault("code", "tool_execution_failed")
        return result

    def check_route(self, request, frame, *, binding=None):
        try:
            demand = dict(request)
            if frame["event_time_hint"]:
                demand["event_time"] = frame["event_time_hint"]
            with timed("capability"):
                route = self.capability.resolve(demand)
        except (ValueError, KeyError, psycopg.Error):
            route = {"trace_id": uuid4().hex, "status": "UNSUPPORTED", "reason_code": "capability_unavailable",
                     "reason": "能力验证暂时不可用。"}
        if binding is not None:
            route = {**route, **binding}
        frame["routes"].append(route)
        return route

    @staticmethod
    def route_outcome(route):
        status = {"DETAIL_QUERY": "detail", "NEEDS_CLARIFICATION": "clarify",
                  "UNSUPPORTED": "unsupported"}.get(route["status"], "error")
        return outcome(status, route.get("reason_code"), route.get("reason", "无法执行此业务路径。"),
                       capability=route)

    def bind_metric(self, request, frame, trace, binding):
        route = self.check_route(request, frame, binding=binding)
        trace.setdefault("capability_records", []).append(route)
        return route

    def query(self, request, route, trace, *, binding=None):
        return self.execute_bound_metric(request, route, trace, binding=binding).result

    def execute_bound_metric(self, request, route, trace, *, binding=None):
        link = {"request": request, "capability_trace_id": route.get("trace_id"), **(binding or {})}
        if capability_link(link, trace.get("capability_records", [])) is not route:
            return metric_failure("invalid_capability_trace_link")
        def execute(query=self.service.query):
            return execute_metric(query, request, required_sources=route["required_fields"].keys())

        work = CURRENT.get()
        hits_before = work.hits if work else 0
        execution = work.control.metric(work, self.service, request, route, execute) if work else execute()
        trace["tools"].append({
            **link, "status": execution.result["status"], "code": execution.code,
            "trace_id": execution.result.get("trace_id"), "sources": execution.result.get("sources", []),
            "elapsed_ms": execution.elapsed_ms,
            **({"cache": "hit"} if work and work.hits > hits_before else {}),
        })
        return execution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    args = parser.parse_args()
    try:
        service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
        capability = CapabilityResolver(CapabilityRegistry(MetadataService.local().snapshot()))
        generate = gemini_generator(dotenv_values(ROOT / ".env"), include_usage=True, schema_for_prompt=schema_for_prompt)
        from .query_asset_governance import QueryAssetRuntime
        from .rag.answer import KnowledgeService
        engine = DynamicSqlEngine.local()
        result = UnifiedAgent(service, capability, DetailQueryExecutor.local(), engine,
                              QueryAssetRuntime.local(engine), KnowledgeService.local(service.metrics)).answer(args.question, generate)
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
