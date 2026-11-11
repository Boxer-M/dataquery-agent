"""Resolve supported Chinese calendar expressions into half-open date windows."""
import re
from datetime import date, timedelta

MONTH = r"[0-9]{4}年[0-9]{1,2}月"
DAY = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
TIME = re.compile(
    rf"(?:从\s*)?{DAY}\s*(?:至|到|~|～)\s*{DAY}|{DAY}|{MONTH}|[0-9]{{4}}年|"
    r"上个月|上月|本月|这个月|去年|今年|昨天|今天"
)


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
