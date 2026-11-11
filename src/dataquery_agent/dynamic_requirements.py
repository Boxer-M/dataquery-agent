"""Explicit dynamic analysis grammar and capability binding."""
import json
import re
from dataclasses import dataclass

from .intent_semantic_resolver import COVERAGE
from .intent_time import TIME, date_window
from .metadata_service import fingerprint
from .metric_query_service import parse_date
from .metrics import ROOT

CONTRACT_PATH = ROOT / "warehouse/dynamic_query_contract.json"
GRAMMAR = (
    ("attempts_by_order", re.compile(r"按订单统计(?:(失败|成功))?支付尝试次数[，,]保留大于([0-9]{1,3})次的订单")),
    ("attempt_rank_by_channel", re.compile(r"各渠道按支付尝试次数列前([0-9]{1,2})订单")),
    ("return_orders_by_category", re.compile(r"按当前品类统计有退货记录的订单数")),
)
ROW_LIMIT = 101


@dataclass(frozen=True)
class DynamicRequirement:
    operation: str
    start: str
    end: str
    threshold: int | None = None
    top_n: int | None = None
    status: str | None = None

    def parameters(self):
        values = {"start": self.start, "end": self.end, "row_limit": ROW_LIMIT}
        for key in ("threshold", "top_n", "status"):
            value = getattr(self, key)
            if value is not None:
                values[key] = value
        return values


def recognize(question, today):
    period = TIME.match(question)
    if not period:
        return None
    body = question[period.end():].strip(" ？?。")
    for operation, pattern in GRAMMAR:
        match = pattern.fullmatch(body)
        if not match:
            continue
        try:
            start, end = date_window(period.group(), today)
        except (ValueError, OverflowError):
            return DynamicRequirement(operation, "", "")
        options = {}
        if operation == "attempts_by_order":
            options = {"threshold": int(match[2]), "status": {None: None, "失败": "failed", "成功": "captured"}[match[1]]}
        elif operation == "attempt_rank_by_channel":
            options = {"top_n": int(match[1])}
        return DynamicRequirement(operation, start.isoformat(), end.isoformat(), **options)
    return None


def approved_spec(requirement):
    if not isinstance(requirement, DynamicRequirement) or not isinstance(requirement.operation, str):
        raise ValueError("invalid_requirement")
    contract = json.loads(CONTRACT_PATH.read_text())
    spec = contract["operations"].get(requirement.operation)
    if spec is None:
        raise ValueError("unsupported_dynamic_operation")
    try:
        start, end = parse_date(requirement.start), parse_date(requirement.end)
    except ValueError:
        raise ValueError("invalid_window") from None
    if not COVERAGE[0] <= start < end <= COVERAGE[1]:
        raise ValueError("outside_verified_window")
    threshold, top, status = requirement.threshold, requirement.top_n, requirement.status
    if requirement.operation == "attempts_by_order":
        if type(threshold) is not int or not 0 <= threshold <= 999 or top is not None or status not in (None, "failed", "captured"):
            raise ValueError("invalid_parameters")
    elif requirement.operation == "attempt_rank_by_channel":
        if type(top) is not int or not 1 <= top <= 20 or threshold is not None or status is not None:
            raise ValueError("invalid_parameters")
    elif any(value is not None for value in (threshold, top, status)):
        raise ValueError("invalid_parameters")
    return contract["version"], spec


def resolve_dynamic(resolver, requirement):
    from .capability_registry import link_key
    from .capability_resolver import CapabilityError

    try:
        version, spec = approved_spec(requirement)
    except ValueError as error:
        return {"status": "UNSUPPORTED", "reason_code": str(error), "reason": "需求超出已批准的动态分析范围。"}
    fields = {name: set(columns) for name, columns in spec["fields"].items()}
    if requirement.status is not None:
        fields[spec["source"]].add("status")
    registry = resolver.registry
    if registry.contract["event_times"].get(spec["source"], {}).get(spec["event_time"]) != spec["time_column"]:
        return {"status": "UNSUPPORTED", "reason_code": "event_time_conflict", "reason": "业务事件时间契约已变化。"}
    try:
        route = resolver.route(spec["source"], fields, {}, set(), standard=False)
    except CapabilityError as error:
        return error.data
    if ({link_key(edge) for edge in route["join_paths"]} != {tuple(edge) for edge in spec["joins"]}
            or any(edge["relationship_cardinality"] not in {"many_to_one", "one_to_one"}
                   for edge in route["join_paths"])):
        return {"status": "UNSUPPORTED", "reason_code": "join_path_unavailable", "reason": "已批准关联无法确认。"}
    return dict(
        route, status="DYNAMIC_QUERY", operation=requirement.operation, definition=spec["definition"],
        event_time=spec["event_time"], time_column=spec["time_column"], grain=spec["grain"],
        result_schema=spec["result_schema"], contract_version=version,
        metadata_version=registry.version, capability_version=fingerprint([spec, registry.version]),
    )
