"""Narrow, explicit knowledge and hybrid routes over the existing metric contract."""
import re

from ..intent_time import TIME, date_window
from ..intent_semantic_resolver import outcome

EXPLANATION = re.compile(r"怎么定义|如何定义|定义是什么|业务定义|口径|有什么区别|有何区别|字段含义|文档|政策|规则")
RETURN_METRICS = ("return_record_count", "return_record_amount", "item_return_rate")
ALIASES = {
    "item_return_rate": ("商品退款率", "商品退货率", "明细退货率", "商品明细退货率"),
    "return_record_count": ("退货记录数", "有几条退货记录"),
    "return_record_amount": ("退货记录金额",),
}
ORDER_RATE = re.compile(r"订单(?:退款|退货)率")
GENERIC_RETURNS = re.compile(r"^退货相关指标是多少[？?，,。\s]*(?:对应的)?业务定义是什么[？?。]?$")


def route_knowledge(agent, question, runtime, frame, trace, today, owner):
    if not EXPLANATION.search(question):
        return None
    service = agent.knowledge
    periods = list(TIME.finditer(question))
    if len(periods) == 1 and GENERIC_RETURNS.fullmatch(TIME.sub("", question).strip()):
        frame["task"] = "hybrid"
        try:
            start, end = date_window(periods[0].group(), today)
        except (ValueError, OverflowError):
            return outcome("clarify", "invalid_time", "请提供有效的指标查询时间。")
        results = []
        for mid in RETURN_METRICS:
            request = {"metric_id": mid, "start_date": start.isoformat(), "end_date": end.isoformat(), "group_by": []}
            if mid == "item_return_rate":
                request["observation_cutoff"] = agent.service.cutoff.date().isoformat()
            result = agent.run_metric_request(request, frame, trace)
            if result["status"] != "ok":
                return result
            results.append(result["query_result"])
        knowledge = service.answer(question, runtime, today=today, owner=owner, topics=list(RETURN_METRICS))
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
        return {"status": "ok", "answer_zh": f"{start} 至 {end}（不含结束日）数据库结果\n"
                + "\n".join(numbers) + "\n文档依据\n" + definitions,
                "metric_results": results, "knowledge_result": knowledge, "path": "hybrid"}
    if periods and re.search(r"多少|排行|同比|环比", question):
        return None
    frame["task"] = "knowledge"
    topics = [mid for mid, metric in agent.service.metrics.items()
              if metric["name_zh"] in question or mid in question or
              any(term in question for term in ALIASES.get(mid, ()))]
    missing = "正式目录没有订单退款率或订单退货率，无法给出该公式。" if ORDER_RATE.search(question) else None
    if missing and "item_return_rate" in topics:
        missing += "商品退款率在这里仅可参考商品明细退货率，该指标按退货记录关联计算，不能用于判断实际退款到账。"
    if missing and not topics:
        return {"status": "ok", "answer_zh": missing, "evidence_status": "insufficient", "citations": [],
                "path": "knowledge", "knowledge_topic": "metric_definition"}
    result = service.answer(question, runtime, today=today, owner=owner, topics=topics or None, missing=missing,
                            rules=not topics and bool(re.search(r"政策|规则", question)))
    trace["tools"].append(result["retrieval_trace"])
    result.update(path="knowledge", knowledge_topic="metric_definition" if topics or missing else "document")
    return result


def knowledge_followup(message, active):
    if (active or {}).get("kind") != "knowledge" or active.get("topic") != "metric_definition":
        return message
    match = re.fullmatch(r"那\s*(.{2,40}?)\s*(?:呢|的定义)[？?。]?", message.strip())
    if match and re.search(r"率|记录数|金额|次数", match[1]):
        return match[1] + "怎么定义？"
    return message
