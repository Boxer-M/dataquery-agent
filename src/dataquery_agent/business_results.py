"""Align, compare and rank metric results without inventing values for missing groups."""
import operator
from decimal import Decimal, ROUND_HALF_UP, localcontext

OPS = {"gt": operator.gt, "lt": operator.lt, "gte": operator.ge, "lte": operator.le}
RATE_METRICS = {
    "payment_attempt_success_rate", "payment_failed_attempt_rate", "cancelled_order_rate",
    "item_return_rate", "customer_repeat_order_rate", "channel_order_share", "category_sales_share",
}

RATIO_COMPONENT_METRICS = RATE_METRICS | {"average_order_record_amount", "orders_per_ordering_customer"}


def decimal_value(value):
    if value is None:
        return None
    if type(value) not in (int, str, Decimal):
        raise ValueError("Metric values must preserve decimal precision.")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Nonfinite metric value.")
    return result


def decimal_text(value):
    return format(value, "f") if value is not None else None


def normalize_rows(rows, *, monthly, has_ratio):
    normalized = {}
    if not isinstance(rows, list):
        raise ValueError("Expected metric result rows.")
    for row in rows:
        if not isinstance(row, dict) or "bucket" not in row or "value" not in row:
            raise ValueError("Missing metric result keys.")
        bucket = row["bucket"]
        month = row.get("month")
        if (bucket is not None and not isinstance(bucket, str)) or (monthly and not isinstance(month, str)):
            raise ValueError("Invalid group key.")
        key = (month if monthly else None, bucket)
        if key in normalized:
            raise ValueError("Duplicate group key.")
        value = decimal_value(row["value"])
        result = {"value": decimal_text(value)}
        if has_ratio:
            numerator = decimal_value(row["numerator"])
            denominator = decimal_value(row["denominator"])
            if numerator is None or denominator is None or denominator < 0:
                raise ValueError("Invalid ratio components.")
            expected = rounded(numerator / denominator) if denominator else None
            if expected != value:
                raise ValueError("Ratio differs from its components.")
            result.update(numerator=decimal_text(numerator), denominator=decimal_text(denominator))
        normalized[key] = result
    return normalized


def rounded(value):
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def compare(mid, baseline, current):
    before = decimal_value(baseline["value"]) if baseline else None
    after = decimal_value(current["value"]) if current else None
    delta = relative = None
    reason = None
    if before is None or after is None:
        reason = "missing_value"
    else:
        delta = after - before
        if before == 0:
            reason = "zero_baseline"
        elif before < 0:
            reason = "negative_baseline"
        else:
            relative = rounded(delta * 100 / before)
    return {
        "baseline": baseline, "current": current,
        "change": decimal_text(delta * 100 if delta is not None and mid in RATE_METRICS else delta),
        "change_unit": "percentage_points" if mid in RATE_METRICS else "metric_unit",
        "change_pct": decimal_text(relative), "change_pct_reason": reason,
    }


def combine(plan, successful):
    keys = set().union(*(rows.keys() for rows in successful.values()))
    order = lambda key: (key[0] or "", key[1] is None, key[1] or "")
    combined = []
    for key in sorted(keys, key=order):
        values = {}
        for mid in plan["metric_ids"]:
            baseline = successful.get((0, mid), {}).get(key)
            if plan["comparison_type"]:
                current = successful.get((1, mid), {}).get(key)
                values[mid] = compare(mid, baseline, current)
            else:
                values[mid] = baseline
        combined.append({"month": key[0], "bucket": key[1], "metrics": values})
    needed = {condition["metric_id"] for condition in plan["conditions"]}
    sort_rule = plan["sort_rule"]
    if sort_rule:
        needed.add(sort_rule["metric_id"])
    if any((0, mid) not in successful for mid in needed):
        return combined, "postprocessing_skipped"

    def value(row, mid):
        cell = row["metrics"][mid]
        return decimal_value(cell["value"]) if cell else None

    for condition in plan["conditions"]:
        threshold = Decimal(condition["value"])
        mid = condition["metric_id"]
        predicate = OPS[condition["operator"]]
        combined = [row for row in combined
                    if (number := value(row, mid)) is not None and predicate(number, threshold)]
    if sort_rule:
        mid = sort_rule["metric_id"]
        direction = -1 if sort_rule["direction"] == "desc" else 1
        combined = [row for row in combined if value(row, mid) is not None]
        combined.sort(key=lambda row: direction * value(row, mid))
    if plan["limit"] is not None:
        combined = combined[:plan["limit"]]
    return combined, None


def with_precision(function, *args, **kwargs):
    with localcontext() as context:
        context.prec = 60
        return function(*args, **kwargs)


def render(plan, rows, tools, metrics, notice):
    windows = "、".join(
        f"[{window['start_date']}, {window['end_date']})" for window in plan["time_windows"]
    )
    lines = [f"统计窗口 {windows}。"]
    if notice:
        lines.append("排序或阈值所需指标查询失败，以下仅展示取得的结果。")
    if any(tool["status"] != "ok" for tool in tools):
        lines.append("部分或全部查询步骤失败，缺失值不参与计算。")
    if not rows:
        lines.append("没有可展示的分组结果。")
    for row in rows[:8]:
        bucket = row["bucket"]
        if bucket is None and set(plan["group_by"]) - {"month"}:
            bucket = "未归类"
        label = " / ".join(part for part in (row["month"], bucket) if part and part != "all") or "汇总"
        parts = []
        for mid, cell in row["metrics"].items():
            name = metrics[mid]["name_zh"]
            if plan["comparison_type"]:
                before = display(mid, cell["baseline"])
                after = display(mid, cell["current"])
                change = cell["change"]
                unit = "个百分点" if cell["change_unit"] == "percentage_points" else ""
                pct = cell["change_pct"]
                reasons = {
                    "zero_baseline": "基期为零",
                    "negative_baseline": "基期为负",
                    "missing_value": "存在缺失值",
                }
                relative = f"{pct}%" if pct is not None else f"未计算（{reasons[cell['change_pct_reason']]}）"
                parts.append(f"{name} {before} → {after}，变化 {change + unit if change is not None else '无结果'}，变化率 {relative}")
            else:
                parts.append(f"{name} {display(mid, cell)}")
        lines.append(f"{label}：{'；'.join(parts)}。")
    if len(rows) > 8:
        lines.append(f"另有 {len(rows) - 8} 组见结构化结果。")
    for mid in plan["metric_ids"]:
        metric = metrics[mid]
        lines.append(f"{metric['name_zh']}按{metric['event_time']}统计。{metric['definition']}")
    for tool in tools:
        if tool.get("trace_id"):
            sources = "、".join(tool.get("sources", [])) or "无"
            lines.append(f"步骤 {tool['step']} · 来源 {sources} · Trace {tool['trace_id']}")
    return "\n".join(lines)


def display(mid, cell):
    if cell is None or cell["value"] is None:
        return "无结果"
    if mid in RATE_METRICS:
        return decimal_text(decimal_value(cell["value"]) * 100) + "%"
    return cell["value"]
