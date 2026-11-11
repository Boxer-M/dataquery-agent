"""Select an approved metric route or explain why a business demand cannot be routed."""
import argparse
import json
from uuid import uuid4

import psycopg

from .capability_registry import CapabilityRegistry, LINK_KEYS, link_key, unique_keys
from .intent_semantic_resolver import ALIASES, COVERAGE
from .metadata_service import MetadataService
from .metric_query_service import RequestError, parse_date

FIELDS = {
    "metric_id", "analysis_id", "start_date", "end_date", "group_by", "filters",
    "observation_cutoff", "event_time", "requested_source", "joins", "expected_metadata_version",
}
STANDARD_FIELDS = {"metric_id", "start_date", "end_date", "group_by", "filters", "observation_cutoff"}
ALLOCATION = "payment_amount_by_category"


class CapabilityError(ValueError):
    def __init__(self, code, reason, *, status="UNSUPPORTED", candidates=None, risks=None):
        super().__init__(reason)
        self.data = {
            "status": status, "reason_code": code, "reason": reason,
            "candidate_sources": candidates or [], "risks": risks or [],
        }


def fail(code, reason, **details):
    raise CapabilityError(code, reason, **details)


def clarify(code, reason):
    fail(code, reason, status="NEEDS_CLARIFICATION")


def candidate(source, reason, selected=False):
    return {"source": source, "reason": reason, "selected": selected}


class CapabilityResolver:
    def __init__(self, registry):
        self.registry = registry

    def resolve(self, demand):
        registry = self.registry
        response = {
            "trace_id": uuid4().hex, "metadata_source": registry.snapshot.source,
            "metadata_version": registry.version, "schema_version": registry.snapshot.schema_version,
            "access_version": registry.access_version,
            "contract_version": registry.contract["version"],
            "metric_version": registry.service.catalog["catalog_version"],
            "selected_source": None, "candidate_sources": [], "required_fields": {},
            "dimensions": [], "join_paths": [], "risks": [],
        }
        try:
            response.update(self._resolve(demand))
        except CapabilityError as error:
            response.update(error.data)
        return response

    def resolve_dynamic(self, requirement):
        from .dynamic_requirements import resolve_dynamic

        return {"trace_id": uuid4().hex, **resolve_dynamic(self, requirement)}

    def _resolve(self, demand):
        r = self.registry
        if not isinstance(demand, dict) or demand.keys() - FIELDS:
            clarify("invalid_request", "请使用约定的结构化能力请求字段。")
        for key in FIELDS - {"group_by", "filters", "joins"}:
            if key in demand and (not isinstance(demand[key], str) or not demand[key].strip()):
                clarify("invalid_request", f"字段 {key} 需要非空文本。")
        if demand.get("expected_metadata_version", r.version) != r.version:
            fail("metadata_version_mismatch", "调用方版本与当前模型契约和数据库结构不一致，请刷新元数据。")
        source = demand.get("requested_source")
        if source and source not in r.models:
            fail("source_forbidden", "请求的来源不在查询能力允许清单内。",
                 candidates=[candidate(source, "未授权的查询来源；ODS 仅可读取技术元数据。")])
        requested_edges = self.requested_joins(demand.get("joins", []))
        mid, analysis = demand.get("metric_id"), demand.get("analysis_id")
        if bool(mid) == bool(analysis):
            clarify("ambiguous_demand", "请明确指定一个指标 ID 或一个已登记的明细分析 ID。")
        dimensions = demand.get("group_by", [])
        if (not isinstance(dimensions, list) or any(not isinstance(axis, str) for axis in dimensions)
                or len(dimensions) != len(set(dimensions))):
            clarify("invalid_dimensions", "分组维度应为无重复的名称列表。")
        if analysis in r.contract["unsupported"]:
            self.unsupported_analysis(analysis)
        if mid == "product_captured_payment_amount" or (
            mid == "captured_payment_amount" and set(dimensions) & {"category", "product"}
        ):
            self.unsupported_analysis(ALLOCATION)
        if mid and mid not in r.service.metrics:
            if mid in ALIASES and len(ALIASES[mid]) > 1:
                choices = "、".join(r.service.metrics[item]["name_zh"] for item in ALIASES[mid])
                clarify("ambiguous_metric", f"请明确需要 {choices} 中的哪一种口径。")
            clarify("unknown_metric", "指标尚无可识别的正式定义，请提供目录中的指标 ID。")
        if mid and mid not in r.standard:
            metric = r.service.metrics[mid]
            status = metric["definition_status"]
            reason = metric.get("deferred_reason") or metric.get("data_quality_limits") or metric["definition"]
            fail("metric_unavailable", reason,
                 status="NEEDS_CLARIFICATION" if status == "needs_definition" else "UNSUPPORTED")
        if analysis or (mid == "item_net_sales_amount" and "order_id" in dimensions):
            return self.detail(analysis or "sales_by_order", demand, dimensions, requested_edges)
        return self.standard(mid, demand, dimensions, requested_edges)

    def requested_joins(self, requested):
        if not isinstance(requested, list) or len(requested) > 6:
            clarify("invalid_join", "关联说明须为最多六条明确的键关系。")
        r = self.registry
        edges = set()
        children_by_key = {}
        for link in requested:
            if (not isinstance(link, dict) or set(link) != set(LINK_KEYS)
                    or any(not isinstance(link[key], str) for key in LINK_KEYS)):
                clarify("invalid_join", "请提供来源、目标及双方的关联字段。")
            origin, target, left, right = link_key(link)
            if origin not in r.models or target not in r.models:
                fail("source_forbidden", "关联引用了未授权来源。")
            for name, column in ((origin, left), (target, right)):
                relation = r.snapshot.get(name)
                if relation is None or column not in relation["columns"]:
                    fail("missing_join_key", f"关联字段 {name}.{column} 不存在。")
            edge = (origin, target, left, right)
            if edge not in r.joins:
                reverse = r.joins.get((target, origin, right, left))
                nonunique_order_keys = (
                    origin.startswith("dwd.") and target.startswith("dwd.") and left == right == "order_id"
                    and (left,) not in unique_keys(r.snapshot.get(origin))
                    and (right,) not in unique_keys(r.snapshot.get(target))
                )
                if (reverse and reverse["relationship_cardinality"] == "many_to_one") or nonunique_order_keys:
                    self.fanout()
                fail("invalid_join", "未找到已批准且目标键唯一的关联。")
            registered = r.joins[edge]
            if origin.startswith("dwd.") and registered["relationship_cardinality"] == "many_to_one":
                children_by_key.setdefault((target, right), set()).add(origin)
            edges.add(edge)
        if any(len(children) > 1 for children in children_by_key.values()):
            self.fanout()
        return edges

    @staticmethod
    def fanout():
        fail("fanout_risk", "该关联组合可能放大事实记录，当前没有批准的跨事实聚合路径。",
             risks=["一对多或多对多关联会重复度量；需要先在相同粒度分别聚合。"])

    def unsupported_analysis(self, analysis):
        reason = self.registry.contract["unsupported"][analysis]
        if analysis == ALLOCATION:
            fail("allocation_missing", reason, candidates=[
                candidate("dws.payment_day", "缺少商品和品类粒度。"),
                candidate("dwd.payment_attempt_fact", "关联多行商品明细会重复支付金额。"),
            ], risks=["订单支付与商品明细为独立事实，按订单号直接拼接后累加会重复支付金额。"])
        fail("missing_source_event", reason)

    @staticmethod
    def time_semantics(demand, expected):
        if demand.get("event_time", expected) != expected:
            clarify("event_time_conflict", f"该业务口径的时间语义为 {expected}，请确认统计口径。")
        try:
            start, end = parse_date(demand.get("start_date")), parse_date(demand.get("end_date"))
        except RequestError:
            clarify("invalid_window", "请提供 YYYY-MM-DD 格式的起止日期。")
        if not COVERAGE[0] <= start < end <= COVERAGE[1]:
            fail("outside_verified_window", "请选择已验证的 2023 至 2025 年半开时间窗口。")
        return {"event_time": expected, "start_date": start.isoformat(), "end_date": end.isoformat(),
                "boundary": "[start_date,end_date)"}

    def standard(self, mid, demand, dimensions, requested_edges):
        r = self.registry
        spec = r.standard[mid]
        window = self.time_semantics(demand, spec["event_time"])
        request = {key: value for key, value in demand.items() if key in STANDARD_FIELDS}
        if spec["observation_cutoff"]:
            request.setdefault("observation_cutoff", spec["observation_cutoff"])
            window["observation_cutoff"] = request["observation_cutoff"]
        try:
            r.service.validate(request)
        except RequestError as error:
            fail(error.code, "该指标服务不支持请求的维度、筛选或观察截止。",
                 candidates=[candidate(spec["source"], "已验收的标准指标模型，但不满足本请求。")])
        fields = {name: set(columns) for name, columns in spec["fields"].items()}
        result = self.route(spec["source"], fields, demand, requested_edges, standard=True)
        result.update(status="STANDARD_METRIC", metric_id=mid, metric_request=request,
                      metric_definition=spec["definition"], dimensions=dimensions, time_constraints=window,
                      grain=r.models[spec["source"]]["business_grain"],
                      reason_code="verified_metric_service", reason="标准模板满足已验证指标口径、时间和维度。")
        result["time_constraints"]["physical_fields"] = r.models[spec["source"]]["event_time"]
        return result

    def detail(self, analysis, demand, dimensions, requested_edges):
        r = self.registry
        spec = r.contract["details"].get(analysis)
        if not spec:
            fail("detail_not_registered", "当前没有该明细分析的已批准能力契约。")
        if set(dimensions) - spec["dimensions"].keys():
            fail("unsupported_grouping", "明细契约没有提供所需的分组维度。")
        filters = demand.get("filters", {})
        if not isinstance(filters, dict) or filters or "observation_cutoff" in demand:
            fail("unsupported_filter", "本明细路径只登记了时间窗口及分组，未登记额外筛选或观察截止。")
        window = self.time_semantics(demand, spec["event_time"])
        fields = {name: set(columns) for name, columns in spec["fields"].items()}
        for axis in dimensions:
            name, column = spec["dimensions"][axis].rsplit(".", 1)
            fields.setdefault(name, set()).add(column)
        result = self.route(spec["source"], fields, demand, requested_edges, standard=False)
        result["candidate_sources"].extend(candidate(name, reason)
                                           for name, reason in spec["rejected_sources"].items())
        result.update(status="DETAIL_QUERY", analysis_id=analysis, metric_id=spec.get("metric_id"),
                      dimensions=dimensions, time_constraints=window, grain=list(dict.fromkeys(spec["grain"] + dimensions)),
                      aggregation_constraints=spec["constraints"], reason_code="detail_grain_required",
                      reason="日汇总不足，已登记的 DWD 路径保留所需键与事件粒度。",
                      execution_policy="plan_only")
        result["time_constraints"]["physical_fields"] = r.models[spec["source"]]["event_time"]
        return result

    def route(self, origin, fields, demand, requested_edges, *, standard):
        r = self.registry
        candidates = [candidate(origin, "契约登记的主事实模型。")]
        if demand.get("requested_source", origin) != origin:
            fail("source_incompatible", "指定来源不符合该指标或明细契约的事实粒度。",
                 candidates=candidates + [candidate(demand["requested_source"], "不是该请求的批准路径。")])
        paths = {}
        for name in list(fields):
            if name in r.errors:
                fail("schema_drift", r.errors[name], candidates=[candidate(name, r.errors[name])])
            path = r.path(origin, name)
            if path is None:
                fail("join_path_unavailable", "不存在可证明目标键唯一的批准关联路径。", candidates=candidates)
            for edge in path:
                paths[link_key(edge)] = edge
                fields.setdefault(edge["from"], set()).add(edge["from_column"])
                fields.setdefault(edge["to"], set()).add(edge["to_column"])
        if requested_edges - paths.keys():
            fail("join_not_required", "请求关联不属于该业务路径，不能用于扩大查询范围。")
        for name, columns in fields.items():
            if name in r.errors:
                fail("schema_drift", r.errors[name], candidates=[candidate(name, r.errors[name])])
            if columns - r.snapshot.get(name)["columns"].keys():
                fail("missing_required_field", "声明的路径缺少必要物理字段。",
                     candidates=[candidate(name, "所需字段未出现在数据库结构中。")])
            if not r.readable(name, columns, standard=standard):
                fail("access_denied", "当前查询角色缺少所需字段的读取权限。",
                     candidates=[candidate(name, "缺少 Schema 使用权或必要字段 SELECT。")])
        risks = list(dict.fromkeys(note for name in fields for note in r.models[name]["quality_limitations"]))
        if any(edge["evidence"] == "business_contract" for edge in paths.values()):
            risks.append("逻辑关联来自验收契约，数据库未为所有关联建立物理外键。")
        return {
            "selected_source": origin, "candidate_sources": [candidate(origin, "满足口径、键及权限检查。", True)],
            "required_fields": {name: sorted(columns) for name, columns in fields.items()},
            "join_paths": list(paths.values()), "risks": risks,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--resolve", help="JSON business demand")
    modes.add_argument("--model", help="Inspect a versioned warehouse relation")
    args = parser.parse_args()
    if args.resolve is not None:
        try:
            demand = json.loads(args.resolve)
        except (ValueError, RecursionError):
            print(json.dumps({"status": "NEEDS_CLARIFICATION", "reason_code": "invalid_request",
                              "reason": "请提供有效的 JSON 业务需求。"}, ensure_ascii=False))
            return 1
    try:
        snapshot = MetadataService.local().snapshot()
        registry = CapabilityRegistry(snapshot)
        if args.model:
            model = registry.models.get(args.model)
            relation = snapshot.get(args.model) if model else None
            result = {"model": model, "technical_metadata": relation}
        else:
            result = CapabilityResolver(registry).resolve(demand)
    except (psycopg.Error, ValueError, KeyError):
        result = {"status": "UNSUPPORTED", "reason_code": "metadata_unavailable",
                  "reason": "无法读取或验证本地元数据。"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("reason_code") == "metadata_unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
