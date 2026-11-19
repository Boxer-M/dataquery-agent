"""Resolve supported Chinese calendar expressions into half-open date windows."""
import re
from datetime import date, timedelta
from copy import deepcopy

MONTH = r"[0-9]{4}年[0-9]{1,2}月"
DAY = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
TIME = re.compile(
    rf"(?:从\s*)?{DAY}\s*(?:至|到|~|～)\s*{DAY}|{DAY}|{MONTH}|[0-9]{{4}}年|"
    r"上个月|上月|本月|这个月|去年|今年|昨天|今天"
)
EXPLICIT_RANGE = re.compile(rf"(?:从\s*)?{DAY}\s*(?:至|到|~|～)\s*{DAY}")
BOUNDARY_OVERRIDE = re.compile(
    # Boundary nouns are unsupported even when their inclusion verb is unfamiliar,
    # moved, or contradictory. Business exclusions such as 不含退款 are not nouns here.
    r"首尾|两端|端点|首日|末日|第一天|最后一天|起始日|开始日|结束日|截止日|"
    r"(?:不含|不包括|包括|包含|含|排除|剔除|不计入)\s*(?:当日|当天)|"
    r"左开|右开|左闭|右闭|开区间|闭区间|半开|inclusive|exclusive|"
    rf"(?:不含|不包括)\s*{DAY}|{DAY}\s*(?:不含|不包括)|"
    rf"{DAY}\s*[（(]\s*(?:含|不含|包含|不包括)\s*[）)]|"
    rf"[\[(（]\s*{DAY}.*{DAY}\s*[\])）]", re.I)


def date_boundary_modifier(question):
    """Locate unsupported calendar-boundary wording in the user source, not business filters."""
    if len(re.findall(DAY, question)) >= 2 and (match := BOUNDARY_OVERRIDE.search(question)):
        return {"source": "user_question", "text": match.group(), "start": match.start(), "end": match.end()}
    return None


def normalize_explicit_windows(question, proposal, today):
    """Prove one closed calendar-date range equivalent; preserve the Validator's original-text gate."""
    expressions = list(dict.fromkeys(m.group() for m in TIME.finditer(question)))
    event = {"status": "not_applicable", "reason": "requires_single_explicit_range"}
    model = proposal["time_windows"]
    audit = {"status": "rejected", "reason": "window_mismatch", "original_question": question,
             "original_proposal": deepcopy(proposal), "source_expressions": expressions,
             "model_expressions": list(model), "normalized_expressions": None,
             "policy": "calendar_dates_inclusive_to_half_open_v1"}
    if modifier := date_boundary_modifier(question):
        audit["reason"] = "unsupported_date_boundary"
        audit["boundary_modifier"] = modifier
        return proposal, audit
    if len(expressions) != 1 or not EXPLICIT_RANGE.fullmatch(expressions[0]):
        return proposal, event
    source, event = expressions[0], audit
    if any(BOUNDARY_OVERRIDE.search(value) for value in model):
        event["reason"] = "unsupported_date_boundary"
        return proposal, event
    if len(model) != 1 or not EXPLICIT_RANGE.fullmatch(model[0]):
        return proposal, event
    try:
        original_window, proposed_window = date_window(source, today), date_window(model[0], today)
        if original_window[0] >= original_window[1] or proposed_window[0] >= proposed_window[1]:
            raise ValueError("invalid date order")
    except (ValueError, OverflowError):
        event["reason"] = "invalid_time"
        return proposal, event
    for key, window in (("source_boundaries", original_window), ("model_boundaries", proposed_window)):
        event[key] = {"start_date": window[0].isoformat(), "end_date": window[1].isoformat(),
                      "start_inclusive": True, "end_exclusive": True, "input_end_inclusive": True}
    if original_window != proposed_window:
        return proposal, event
    normalized = dict(proposal, time_windows=[source])
    event.update(status="unchanged" if model == [source] else "normalized",
                 reason="equivalent_explicit_date_boundaries", normalized_expressions=[source])
    return normalized, event


def next_month(day):
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def date_window(expression, today):
    if expression in {"今天", "昨天"}:
        start = today - timedelta(days=expression == "昨天")
        return start, start + timedelta(days=1)
    if expression in {"上个月", "上月", "本月", "这个月"}:
        start = today.replace(day=1)
        if expression in {"上个月", "上月"}:
            start = (start - timedelta(days=1)).replace(day=1)
        return start, next_month(start)
    if expression in {"去年", "今年"}:
        expression = f"{today.year - (expression == '去年')}年"
    if re.fullmatch(MONTH, expression):
        year, month = map(int, re.findall(r"[0-9]+", expression))
        start = date(year, month, 1)
        return start, next_month(start)
    if re.fullmatch(r"[0-9]{4}年", expression):
        year = int(expression[:-1])
        return date(year, 1, 1), date(year + 1, 1, 1)
    days = re.findall(DAY, expression)
    start = date.fromisoformat(days[0])
    return start, date.fromisoformat(days[-1]) + timedelta(days=1)
