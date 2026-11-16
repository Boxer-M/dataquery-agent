"""Project chart metadata from verified metric calls and the published catalog."""
import json
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from .metric_query_service import CATALOG, RequestError, parse_date, windows


class ChartMetric(BaseModel):
    metric_id: str
    name: str
    unit: str
    unit_type: Literal["count"]
    sources: list[str]


class ChartDimension(BaseModel):
    field: Literal["month", "bucket"]
    type: Literal["time", "category"]
    semantic: Literal["month", "channel"]
    order: Literal["calendar_asc", "category_code_asc"]


class ChartWindow(BaseModel):
    start_date: str
    end_date: str
    end_exclusive: Literal[True] = True


class Visualization(BaseModel):
    version: Literal["1"] = "1"
    metric: ChartMetric
    dimension: ChartDimension
    window: ChartWindow
    value_path: list[str]
    row_order: list[int]


@lru_cache(maxsize=1)
def chart_catalog():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    units = catalog.get("visualization_units", {})
    return {metric["metric_id"]: (metric, units[metric["metric_id"]])
            for metric in catalog["metrics"] if metric["metric_id"] in units}


def chart_query(result):
    if query := result.get("query_result"):
        if query.get("status") == "ok":
            return query, query.get("sources"), ["value"]
        return None
    plan, tools = result.get("plan"), result.get("tools", [])
    if (not plan or len(plan.get("metric_ids", [])) != 1 or len(tools) != 1
            or plan.get("comparison_type") or plan.get("conditions")
            or plan.get("sort_rule") or plan.get("limit") is not None or tools[0].get("status") != "ok"):
        return None
    request = tools[0]["request"]
    if plan["metric_ids"] != [request["metric_id"]]:
        return None
    return request, tools[0].get("sources"), ["metrics", request["metric_id"], "value"]


def chart_metadata(result, rows, *, status, truncated, summary_only):
    if summary_only:
        return None, "summary_only"
    if truncated:
        return None, "rows_truncated"
    if status != "completed":
        return None, "query_not_completed"
    if len(rows) < 2:
        return None, "insufficient_rows"
    query = chart_query(result)
    if not query:
        return None, "unsupported_query_shape"
    request, sources, value_path = query
    groups = request.get("group_by")
    if groups not in (["month"], ["channel"]):
        return None, "unsupported_dimension"
    approved = chart_catalog().get(request.get("metric_id"))
    if not approved:
        return None, "unit_not_approved"
    metric, unit = approved
    if (metric["dws_validation"]["status"] != "available" or not metric.get("name_zh")
            or sources != [metric.get("existing_summary")]):
        return None, "unverified_metadata"
    try:
        start = parse_date(request.get("start_date"))
        end = parse_date(request.get("end_date"))
    except RequestError:
        return None, "invalid_window"
    if start >= end:
        return None, "invalid_window"

    for row in rows:
        value = row
        for key in value_path:
            value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            return None, "invalid_value"

    if groups == ["month"]:
        expected = list(windows(start, end, monthly=True))
        positions = {lower.strftime("%Y-%m"): index for index, (lower, _) in enumerate(expected)}
        order = []
        for row in rows:
            position = positions.get(row.get("month"))
            if position is None or row.get("bucket") != "all":
                return None, "invalid_dimension_rows"
            lower, upper = expected[position]
            if value_path == ["value"] and (row.get("start_date"), row.get("end_date")) != (
                lower.isoformat(), upper.isoformat()
            ):
                return None, "invalid_dimension_rows"
            order.append(position)
        if sorted(order) != list(range(len(expected))):
            return None, "invalid_dimension_rows"
        dimension = ChartDimension(field="month", type="time", semantic="month", order="calendar_asc")
    else:
        order = [row.get("bucket") for row in rows]
        if (any(row.get("month") is not None for row in rows)
                or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key)
                       for key in order)
                or len(set(order)) != len(order)):
            return None, "invalid_dimension_rows"
        dimension = ChartDimension(field="bucket", type="category", semantic="channel", order="category_code_asc")
    return Visualization(
        metric=ChartMetric(metric_id=request["metric_id"], name=metric["name_zh"], sources=sources, **unit),
        dimension=dimension,
        window=ChartWindow(start_date=start.isoformat(), end_date=end.isoformat()),
        value_path=value_path,
        row_order=sorted(range(len(rows)), key=order.__getitem__),
    ), None
