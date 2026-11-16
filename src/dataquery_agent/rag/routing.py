"""Narrow, explicit knowledge and hybrid routes over the existing metric contract."""
import re

from ..intent_time import TIME, date_window
from ..intent_semantic_resolver import ALIASES, outcome
from ..metric_entity import DEFINITION_ALIASES, MetricEntityResolver, normalize
from .documents import CATALOG_PATH

EXPLANATION = re.compile(r"怎么定义|如何定义|定义是什么|业务定义|口径|有什么区别|有何区别|字段含义|文档|政策|规则|指标定义|正式定义|怎么算")
RETURN_METRICS = ("return_record_count", "return_record_amount", "item_return_rate")
DEFINITION = re.compile(r"怎么定义|如何定义|定义是什么|业务定义|指标定义|口径|怎么算|有什么区别|有何区别")
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
    suffix = CATALOG_SUFFIX.search(question)
    if not bare_id and not suffix and not EXPLANATION.search(question):
        return None
    service = agent.knowledge
    periods = list(TIME.finditer(question))
    if len(periods) == 1 and GENERIC_RETURNS.fullmatch(TIME.sub("", question).strip()):
        frame["task"] = "hybrid"
        try:
            start, end = date_window(periods[0].group(), today)
        except (ValueError, OverflowError):
            return outcome("clarify", "invalid_time", "请提供有效的指标查询时间。")
        results, tasks, calls = [], [], []
        for mid in RETURN_METRICS:
            request = {"metric_id": mid, "start_date": start.isoformat(), "end_date": end.isoformat(), "group_by": []}
            if mid == "item_return_rate":
                request["observation_cutoff"] = agent.service.cutoff.date().isoformat()
            calls.append({"request": request})
        plan = {"metric_ids": list(RETURN_METRICS), "required_tool_calls": calls,
                "include_catalog_definitions": True, "analysis_type": "legacy_return_summary"}
        if owner:
            from ..catalog_recovery import checkpoint_partial, sql_checkpoint
            routes = [agent.check_route(call["request"], frame) for call in calls]
            if any(route["status"] != "STANDARD_METRIC" for route in routes):
                return agent.route_outcome(next(route for route in routes if route["status"] != "STANDARD_METRIC"))
            before = sql_checkpoint(agent.service, plan, routes, capture_snapshot=True)
        else:
            before = None
        for call in calls:
            request = call["request"]
            mid = request["metric_id"]
            result = agent.run_metric_request(request, frame, trace)
            if result["status"] != "ok":
                return result
            results.append(result["query_result"])
            tasks.append({"kind": "metric", "metric_id": mid, "status": "ok",
                          "sources": results[-1]["sources"], "trace_id": results[-1]["trace_id"]})
        resolution = {"method": "approved_hybrid", "metric_ids": list(RETURN_METRICS)}
        after = sql_checkpoint(agent.service, plan, routes, capture_snapshot=True) if before else None
        knowledge = service.exact(agent.service, resolution, today=today, owner=owner)
        trace["tools"].append(knowledge["retrieval_trace"])
        numbers = []
        for result in results:
            name = agent.service.metrics[result["metric_id"]]["name_zh"]
            row = result["results"][0]
            value = row["value"] if row["value"] is not None else "无可计算结果"
            extra = (f"（{row['numerator']}/{row['denominator']}，观察截止 {result['observation_cutoff']}）"
                     if result["metric_id"] == "item_return_rate" else "")
            numbers.append(f"{name} {value}{extra} · {', '.join(result['sources'])}")
        definitions = knowledge.get("answer_zh") or knowledge.get("message", "")
        sql_answer = f"{start} 至 {end}（不含结束日）数据库结果\n" + "\n".join(numbers)
        tasks += catalog_tasks(knowledge, RETURN_METRICS)
        status = "ok" if knowledge["evidence_status"] == "supported" else "partial"
        saved = {}
        if status == "partial" and before and before == after:
            proof = {key: value for key, value in before.items() if key != "snapshot"}
            saved, tasks = checkpoint_partial(plan, tasks, proof, results=[], sql_answer=sql_answer,
                                              metric_results=results)
        return {"status": status, "answer_zh": sql_answer + "\n文档依据\n" + definitions,
                "metric_results": results, "knowledge_result": knowledge, "path": "hybrid",
                "subtasks": tasks, "metric_resolution": resolution, **saved}
    if suffix and periods and re.search(r"查询|多少|排行|对比|比较|环比|统计|查看", question[:suffix.start()]):
        numeric = question[:suffix.start()].strip()
        resolution = agent.planner.validator.entities.resolve(numeric)
        frame["task"] = "hybrid"
        return agent.business(numeric, runtime, frame, trace, today, catalog=resolution, owner=owner)
    if periods and re.search(r"多少|排行|同比|环比", question):
        return None
    frame["task"] = "knowledge"
    if bare_id or (DEFINITION.search(question) and not re.search(r"字段含义|政策|文档", question)):
        resolution = MetricEntityResolver(agent.service.metrics, ALIASES | DEFINITION_ALIASES).resolve(ORDER_RATE.sub(" ", question))
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
    result = service.answer(question, runtime, today=today, owner=owner, rules=bool(re.search(r"政策|规则", question)))
    trace["tools"].append(result["retrieval_trace"])
    return dict(result, path="knowledge", knowledge_topic="document")


def knowledge_followup(message, active):
    if (active or {}).get("kind") != "knowledge" or active.get("topic") != "metric_definition":
        return message
    match = re.fullmatch(r"那\s*(.{2,40}?)\s*(?:呢|的定义)[？?。]?", message.strip())
    if match and re.search(r"率|记录数|金额|次数|[a-z]+_[a-z_]+", match[1]):
        return match[1] + "怎么定义？"
    return message
