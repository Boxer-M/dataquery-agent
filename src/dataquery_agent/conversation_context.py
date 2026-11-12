"""Versioned deterministic follow-up rules. Only the current turn is sent to a model."""
import re
from datetime import date, datetime, timedelta, timezone

from .intent_semantic_resolver import CATEGORY, MODIFIERS
from .intent_time import TIME, date_window, next_month

CONTEXT_VERSION = "conversation-rules-v1"
CLARIFICATION_MINUTES = 15
MONTH = re.compile(r"(?:那)?(?:([0-9]{4})年)?([0-9]{1,2})月(?:份)?(?:呢)?")
CHANNEL = re.compile(r"只看(web|mobile|partner|phone)渠道", re.I)
GROUPS = {"按渠道拆分": ["channel"], "按月拆分": ["month"], "不分组": []}
RESET = {"重新开始一个分析", "重新开始", "重置会话"}
SECRET = re.compile(
    r"(?i)postgres(?:ql)?://\S+|AIza[\w-]{25,}|gh[pousr]_[\w]{20,}|"
    r"bearer\s+[\w.\-]+|(?:api[_ -]?key|password|token|密码|密钥)\s*[:=：]\s*[^\s，。;]+|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\w)[0-9a-f]{64}(?!\w)|(?<![0-9])1[3-9][0-9]{9}(?![0-9])"
)


def redact(text):
    return SECRET.sub("[已脱敏]", text)


class ContextError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def month_request(request, year, month):
    try:
        start = date(year, month, 1)
        end = next_month(start)
    except (ValueError, OverflowError):
        raise ContextError("invalid_month", "请提供有效月份。") from None
    return dict(request, start_date=start.isoformat(), end_date=end.isoformat())


def compare_plan(request):
    start, end = (date.fromisoformat(request[key]) for key in ("start_date", "end_date"))
    if start.day != 1 or next_month(start) != end or "month" in request.get("group_by", []):
        raise ContextError("incomparable_window", "请先明确一个完整自然月及可比较的分组。")
    previous = (start - timedelta(days=1)).replace(day=1)
    windows = [{"start_date": previous.isoformat(), "end_date": start.isoformat()},
               {"start_date": start.isoformat(), "end_date": end.isoformat()}]
    return {
        "analysis_type": "comparison", "metric_ids": [request["metric_id"]],
        "time_windows": windows, "group_by": request.get("group_by", []),
        "filters": request.get("filters", {}), "comparison_type": "mom",
        "sort_rule": None, "limit": None, "conditions": [], "unresolved_business_rules": [],
        "required_tool_calls": [{"step": index + 1, "window_index": index, "request": request | window}
                                for index, window in enumerate(windows)],
    }


def edit_for_message(message, context, metrics, cutoff):
    text = message.strip().rstrip("。！？!?")
    month, channel = MONTH.fullmatch(text), CHANNEL.fullmatch(text)
    switch = re.fullmatch(r"换成(.+)", text)
    is_edit = month or channel or switch or text in {*GROUPS, "和上个月比较", "取消渠道筛选", "所有渠道"}
    if not is_edit:
        if text.startswith(("那", "继续", "只看", "和上个月")):
            raise ContextError("unknown_followup", "请明确要继承或修改的条件。")
        return None
    if not context or context.get("kind") != "standard":
        raise ContextError("no_compatible_context", "请先执行一项可继续追问的标准指标查询。")
    request = dict(context["request"])
    if month:
        year = int(month[1]) if month[1] else date.fromisoformat(request["start_date"]).year
        return "time", month_request(request, year, int(month[2]))
    if channel:
        return "channel", dict(request, filters=request.get("filters", {}) | {"channel": channel[1].lower()})
    if text in {"取消渠道筛选", "所有渠道"}:
        return "remove_channel", dict(request, filters={key: value for key, value in request.get("filters", {}).items()
                                                       if key != "channel"})
    if text in GROUPS:
        return "grouping", dict(request, group_by=GROUPS[text])
    if switch:
        matched = [mid for mid, item in metrics.items() if switch[1] in {mid, item["name_zh"]}]
        if len(matched) != 1:
            raise ContextError("unknown_metric_choice", "请使用正式指标名称切换指标。")
        request["metric_id"] = matched[0]
        request.pop("observation_cutoff", None)
        if matched[0] == "item_return_rate":
            request["observation_cutoff"] = cutoff
        return "metric", request
    return "comparison", compare_plan(request)


def pending_for(answer, question, today, semantic, now=None):
    match = answer.get("match") or {}
    evidence = match.get("original_evidence")
    choices = answer.get("choices", [])
    periods = [item.group() for item in TIME.finditer(question)]
    if (answer.get("code") != "ambiguous_metric" or not isinstance(evidence, str)
            or question.count(evidence) != 1 or not choices or len(periods) != 1
            or MODIFIERS.search(question.replace(evidence, "", 1))
            or re.search(r"下单的|下单时间|支付时间|退货时间|按下单日|按支付日|按退货日", question)):
        return None
    # Validate all unrelated constraints against the existing single-metric parser.
    groups = [item.group() for item in semantic.group_pattern.finditer(question)]
    filters = [item.group() for item in semantic.channel_pattern.finditer(question)]
    filters += [item.group() for item in CATEGORY.finditer(question)]
    try:
        start, end = date_window(periods[0], today)
    except (ValueError, OverflowError):
        return None
    clock = now or datetime.now(timezone.utc)
    return {
        "context_protocol": CONTEXT_VERSION, "slot": "metric_id", "choices": [{"metric_id": item["metric_id"], "name": item["name"]} for item in choices],
        "original_question": redact(question), "evidence": evidence, "time_evidence": periods[0],
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "group_evidence": groups, "filter_evidence": filters,
        "expires_at": (clock + timedelta(minutes=CLARIFICATION_MINUTES)).isoformat(),
    }


def resume_request(pending, message, semantic, now=None):
    clock = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(pending["expires_at"]) <= clock:
        raise ContextError("clarification_expired", "该澄清已过期，请重新描述完整问题。")
    text = message.strip().rstrip("。！？!?")
    selected = [item for item in pending["choices"] if text in (item["name"], item["metric_id"])]
    if len(selected) != 1:
        raise ContextError("invalid_clarification_choice", "请选择上轮给出的业务口径。")
    item = selected[0]
    question = pending["original_question"].replace(pending["evidence"], item["name"], 1)
    intent = {
        "metrics": [item["name"]], "time": pending["time_evidence"],
        "groups": pending["group_evidence"], "filters": pending["filter_evidence"],
        "missing": [], "type": "metric",
    }
    # Freeze the original resolved time, including relative expressions, across dates.
    frozen = question.replace(pending["time_evidence"], pending["start_date"] + "到" +
                              (date.fromisoformat(pending["end_date"]) - timedelta(days=1)).isoformat(), 1)
    intent["time"] = next(TIME.finditer(frozen)).group()
    resolved = semantic.resolve(frozen, intent, clock.date())
    if resolved.get("status") != "ready":
        raise ContextError(resolved.get("code", "unsupported_context"),
                           resolved.get("message", "此口径与原问题条件不兼容。"))
    return resolved["request"]
