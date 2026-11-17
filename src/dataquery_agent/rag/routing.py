"""Narrow, explicit knowledge and hybrid routes over the existing metric contract."""
import re

import psycopg
from uuid import uuid4

from ..intent_time import TIME, date_window
from ..intent_semantic_resolver import ALIASES, outcome
from ..metric_entity import DEFINITION_ALIASES, MetricEntityResolver, normalize
from ..runtime_trace import record_agent_calls
from .documents import CATALOG_PATH, KnowledgeError

EXPLANATION = re.compile(r"怎么定义|如何定义|定义是什么|业务定义|口径|有什么区别|有何区别|字段含义|文档|政策|规则|指标定义|正式定义|怎么算")
RETURN_METRICS = ("return_record_count", "return_record_amount", "item_return_rate")
DEFINITION = re.compile(r"怎么定义|如何定义|定义是什么|业务定义|指标定义|口径|怎么算|有什么区别|有何区别")
PDF = re.compile(r"(?<![a-z0-9])pdf(?![a-z0-9])", re.I)
FORMAL_DEFINITION = re.compile(r"正式(?:指标|业务)?(?:定义|口径)|指标定义")
NUMERIC = re.compile(r"多少|排行|同比|环比|统计|汇总|怎么分布|如何分布|按.{1,8}(?:拆分|分组)")
NO_VALUE = re.compile(r"(?:不必|不用|无需|不要|不需要)\s*(?:查询|统计|计算|给出)?\s*(?:数值|结果|实际数据)")
NO_DEFINITION = re.compile(r"(?:不必|不用|无需|不要|不需要)\s*(?:解释|给出|说明|引用)?\s*(?:正式)?(?:定义|口径)")
DOCUMENT_PAGE = re.compile(r"扫描页|扫描件|原图坐标|原件上的.{0,6}坐标")
ORDER_RATE = re.compile(r"订单(?:退款|退货)率")
GENERIC_RETURNS = re.compile(r"^退货相关指标是多少[？?，,。\s]*(?:对应的)?业务定义是什么[？?。]?$")

CATALOG_SUFFIX = re.compile(
    r"[，,。；;？?\s]*(?:并(?:给出|附上|说明)|同时(?:给出|附上|说明)|(?:对应的)?)(?:各(?:项)?|它们的)?"
    r"(?:正式)?(?:指标|业务)?定义(?:是什么)?[？?。]?$"
)


def catalog_tasks(knowledge, metric_ids):
    citations = {item["source_uri"]: item for item in knowledge["citations"]}
    trace = knowledge["retrieval_trace"]
    tasks = []
    for mid in metric_ids:
        source = f"{CATALOG_PATH}#/metrics[metric_id={mid}]"
        citation = citations.get(source)
        tasks.append({"kind": "catalog", "metric_id": mid, "status": "ok" if citation else "unavailable",
                      "sources": [source], "trace_id": trace["trace_id"],
                      "error_code": None if citation else trace.get("metric_errors", {}).get(mid, trace.get("reason")),
                      "citation": citation})
    return tasks


def route_knowledge(agent, question, runtime, frame, trace, today, owner):
    bare_id = normalize(question.strip().rstrip("？?。")) in agent.service.metrics
    negates_definition = bool(NO_DEFINITION.search(question))
    suffix = None if negates_definition else CATALOG_SUFFIX.search(question)
    mentions_pdf = bool(PDF.search(question) or DOCUMENT_PAGE.search(question))
    formal_definition = bool(FORMAL_DEFINITION.search(question) and not negates_definition)
    if not bare_id and not suffix and not EXPLANATION.search(question) and not mentions_pdf:
        return None
    service = agent.knowledge
    periods = list(TIME.finditer(question))
    pdf_clause = re.search(r"(?:并|同时)\s*(?:请\s*)?(?:引用|摘录|结合|对照|参考)\s*(?P<subject>.+)",
                           question)
    if pdf_clause and PDF.search(pdf_clause["subject"]):
        numeric = question[:pdf_clause.start()].strip(" ，,；;")
        entities = agent.planner.validator.entities.resolve(numeric)
        if (entities["metric_ids"] or entities["unknown_ids"]
                or (TIME.search(numeric) and re.search(r"查询|统计|多少", numeric))):
            frame["task"] = "hybrid"
            subject = pdf_clause["subject"].strip()
            if FORMAL_DEFINITION.search(subject):
                return outcome("clarify", "pdf_formal_definition", "正式指标定义来自指标目录。请指定另需引用的 PDF 主题。")
            if not re.sub(r"已审核|已发布|文档|内容|说明|[的和\W_]", "", PDF.sub("", subject)):
                return outcome("clarify", "missing_pdf_scope", "请提供要引用的 PDF 主题、规则或文档名称。")
            if not owner:
                return outcome("unsupported", "pdf_owner_required", "请在已授权的会话中查询 PDF。")
            return agent.business(numeric, runtime, frame, trace, today, owner=owner, pdf_question=subject)
    if len(periods) == 1 and GENERIC_RETURNS.fullmatch(TIME.sub("", question).strip()):
        frame["task"] = "hybrid"
        try:
            start, end = date_window(periods[0].group(), today)
        except (ValueError, OverflowError):
            return outcome("clarify", "invalid_time", "请提供有效的指标查询时间。")
        calls = []
        window = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        for step, mid in enumerate(RETURN_METRICS, 1):
            request = {"metric_id": mid, **window, "group_by": []}
            if mid == "item_return_rate":
                request["observation_cutoff"] = agent.service.cutoff.date().isoformat()
            calls.append({"step": step, "window_index": 0, "request": request})
        plan = {"metric_ids": list(RETURN_METRICS), "required_tool_calls": calls, "analysis_type": "summary",
                "time_windows": [window], "group_by": [], "conditions": [], "sort_rule": None,
                "comparison_type": None, "limit": None}
        return agent.run_hybrid_plan(plan, {"method": "approved_hybrid"}, frame, trace, today, owner,
                                     keep_metric_results=True)
    if suffix and periods and re.search(r"查询|多少|排行|对比|比较|环比|统计|查看", question[:suffix.start()]):
        numeric = question[:suffix.start()].strip()
        resolution = agent.planner.validator.entities.resolve(numeric)
        frame["task"] = "hybrid"
        return agent.business(numeric, runtime, frame, trace, today, catalog=resolution, owner=owner)
    if periods and NUMERIC.search(question) and not formal_definition and not mentions_pdf and not NO_VALUE.search(question):
        return None
    frame["task"] = "knowledge"
    definition_question = not negates_definition and (formal_definition or DEFINITION.search(question))
    document_context = mentions_pdf or re.search(r"字段含义|政策|文档", question)
    resolution = None
    if bare_id or definition_question:
        resolution = MetricEntityResolver(agent.service.metrics, ALIASES | DEFINITION_ALIASES).resolve(
            ORDER_RATE.sub(" ", question))
    formal_reference = resolution and (resolution["metric_ids"] or resolution["unknown_ids"] or ORDER_RATE.search(question))
    if resolution is not None and (bare_id or formal_reference or not document_context
                                   or (mentions_pdf and formal_definition)):
        ids = resolution["metric_ids"]
        methods = {match["method"] for match in resolution["matches"]}
        resolution["method"] = next(iter(methods)) if len(methods) == 1 else "mixed" if methods else "not_found"
        missing = "正式目录没有订单退款率或订单退货率，无法给出该公式。" if ORDER_RATE.search(question) else None
        if resolution["status"] == "ambiguous":
            result = agent.semantic.clarify_metric(list(dict.fromkeys(mid for match in resolution["matches"]
                                                         if len(match["metric_ids"]) > 1 for mid in match["metric_ids"])))
            result.update(path="knowledge", knowledge_topic="metric_definition", metric_resolution=resolution,
                          evidence_status="insufficient", citations=[])
            trace["tools"].append({"strategy": "catalog_clarification", "reason": result["code"],
                                   "query_embedding_called": False, "metric_ids": ids,
                                   "resolution_method": resolution["method"]})
            return result
        if len(ids) > 5:
            return outcome("unsupported", "metric_limit", "一次最多查询五个正式指标。")
        if missing and "item_return_rate" in ids:
            missing += "商品退款率在这里仅可参考商品明细退货率，该指标按退货记录关联计算，不能用于判断实际退款到账。"
        if resolution["unknown_ids"]:
            missing = "正式目录未登记这些指标 ID：" + "、".join(resolution["unknown_ids"]) + "。"
        if not ids and not missing:
            missing = "正式目录未找到对应指标定义，请提供指标 ID 或正式名称。"
        result = service.exact(agent.service, resolution, today=today, owner=owner, missing=missing)
        trace["tools"].append(result["retrieval_trace"])
        return dict(result, path="knowledge", knowledge_topic="metric_definition", metric_resolution=resolution)
    if agent.supervisor and mentions_pdf:
        if not owner:
            return outcome("unsupported", "pdf_owner_required", "请在已授权的会话中查询 PDF。")
        from ..supervisor_graph import role_context, run_supervisor

        def execute(roles):
            if "knowledge_agent" not in roles:
                return outcome("error", "knowledge_delegation_declined", "PDF 检索未获专业角色批准。")
            return run_knowledge(agent, question, runtime, trace, today, owner, mentions_pdf)

        return run_supervisor(runtime, trace, contexts={
            "knowledge_agent": role_context(question, source="reviewed_pdf"),
        }, execute=execute)
    return run_knowledge(agent, question, runtime, trace, today, owner, mentions_pdf)


def run_knowledge(agent, question, runtime, trace, today, owner, mentions_pdf):
    from ..specialists import KnowledgeAgent

    service = agent.knowledge
    kind = "business_rule" if re.search(r"政策|规则", question) else "technical_document"
    specialist = KnowledgeAgent(
        lookup_catalog=None,
        answer_document=lambda subject, rules: service.answer(subject, runtime, today=today, owner=owner, rules=rules,
                                                                   pdf_only=mentions_pdf),
    )
    result = specialist.execute(kind, question, known_metrics=agent.service.metrics, runtime=runtime)
    plan_id, attempt_id = uuid4().hex, uuid4().hex
    binding = {"plan_id": plan_id, "task_id": f"{plan_id}/1", "attempt_id": attempt_id}
    retrieval = result.get("retrieval_trace")
    if retrieval:
        retrieval.update(binding, agent="knowledge", tool="rag_retrieval", kind="knowledge", status=result["status"])
        trace["tools"].append(retrieval)
    task = {**binding, "kind": "knowledge", "status": result["status"], "agent_call": result["agent_call"]}
    record_agent_calls(trace, [task], plan_id=plan_id)
    if retrieval:
        task["trace_id"] = retrieval["trace_id"]
    return dict(result, path="knowledge", knowledge_topic="document", plan_id=plan_id, attempt_id=attempt_id,
                subtasks=[task])



def run_document_plan(agent, plan, runtime, frame, trace, today, owner, *, roles=None):
    """Run the validated metric and scoped PDF through the existing DAG."""
    from ..specialists import KnowledgeAgent

    specialist = KnowledgeAgent(
        lookup_catalog=None,
        answer_document=lambda question, _: agent.knowledge.answer(question, runtime, today=today,
                                                                    owner=owner, pdf_only=True),
    )

    def document(params):
        if roles is not None and "knowledge_agent" not in roles:
            return {"status": "unavailable", "error_code": "knowledge_delegation_declined"}, None
        evidence = specialist.execute("technical_document", params["question"],
                                      known_metrics=agent.service.metrics, runtime=runtime)
        citations = evidence["citations"]
        if citations:
            try:
                current = agent.knowledge.retriever.store.current_pdf_citations(citations, owner=owner)
            except (psycopg.Error, KnowledgeError, OSError, ValueError):
                current = []
            if len(current) != len(citations):
                evidence.update(citations=[], answer_zh="PDF 证据版本或权限已变化。",
                                evidence_status="unavailable", code="pdf_evidence_invalidated")
        state = evidence["evidence_status"]
        status = {"supported": "ok", "partial": "partial"}.get(state, "unavailable")
        code = evidence.get("code") or (None if status == "ok" else f"pdf_evidence_{state}")
        retrieval = evidence.get("retrieval_trace", {})
        return {"status": status, "error_code": code, "agent_call": evidence["agent_call"],
                "trace_id": retrieval.get("trace_id"), "elapsed_ms": retrieval.get("elapsed_ms"),
                "sources": list(dict.fromkeys(item["source_uri"] for item in evidence["citations"]))}, evidence

    result = agent.run_business_plan(plan, frame, trace, document=document, roles=roles)
    if "pdf_result" not in result:
        return result
    metric, pdf, summary = result["subtasks"]
    knowledge = result.pop("pdf_result") or {"evidence_status": "unavailable", "citations": []}
    assembled = result.pop("pdf_summary")
    for task in result["subtasks"]:
        task["plan_id"] = result["plan_id"]
    record_agent_calls(trace, (metric, pdf), plan_id=result["plan_id"])
    if retrieval := knowledge.get("retrieval_trace"):
        trace["tools"].append({**retrieval, **{key: pdf[key] for key in
                               ("plan_id", "task_id", "attempt_id", "kind", "status", "error_code", "sources")},
                               "agent": "knowledge", "tool": "rag_retrieval", "scope": "reviewed_pdf"})

    status = "ok" if summary["status"] == "ok" else (
        "partial" if metric["status"] == "ok" or knowledge["citations"] else "error")
    code = (summary.get("error_code") if metric["status"] == pdf["status"] == "ok"
            else pdf.get("error_code") if metric["status"] == "ok" else metric.get("error_code"))
    sql_text = result["answer_zh"] if metric["status"] == "ok" else "SQL 指标数值不可用。"
    pdf_text = knowledge.get("answer_zh") if knowledge["citations"] else "缺少可验证的 PDF 证据。"
    return dict(result, status=status, code=code, path="sql_document_hybrid", knowledge_result=knowledge,
                answer_zh=assembled or f"数据库结果\n{sql_text}\n\nPDF 证据\n{pdf_text}")

def knowledge_followup(message, active):
    if (active or {}).get("kind") != "knowledge" or active.get("topic") != "metric_definition":
        return message
    match = re.fullmatch(r"那\s*(.{2,40}?)\s*(?:呢|的定义)[？?。]?", message.strip())
    if match and re.search(r"率|记录数|金额|次数|[a-z]+_[a-z_]+", match[1]):
        return match[1] + "怎么定义？"
    return message
