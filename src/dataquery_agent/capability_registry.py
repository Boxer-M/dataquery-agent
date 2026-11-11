"""Derive query capabilities from versioned contracts and a live catalog snapshot."""
import json
import re
from hashlib import sha256
from .metadata_service import MANIFESTS, manifest_models, fingerprint
from .metric_query_service import CATALOG, REGISTRY, TEMPLATES, SHARE_AXES, MetricQueryService
from .metrics import ROOT

CONTRACT = "warehouse/capability_contract.json"
RELATIONS = re.compile(r"\b(?:FROM|JOIN)\s+((?:dws|dim)\.[a-z_]+)", re.I)
LINK_KEYS = ("from", "to", "from_column", "to_column")
TIME_BY_FAMILY = {"payments": "payment_attempt_time", "returns": "return_record_time"}
TIMESTAMP_TYPES = {"date", "timestamp without time zone"}


def unique_keys(relation):
    return [tuple(key["columns"]) for key in relation["constraints"]
            if key["kind"] in {"p", "u"} and key["validated"]]


def link_key(link):
    return tuple(link[field] for field in LINK_KEYS)


class CapabilityRegistry:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.contract = json.loads((ROOT / CONTRACT).read_text())
        self.manifests = manifest_models()
        self.service = MetricQueryService(None)
        assets = set(MANIFESTS) | {
            CONTRACT, str(CATALOG.relative_to(ROOT)), str(REGISTRY.relative_to(ROOT)),
            str(TEMPLATES.relative_to(ROOT)), "sql/load_sqlshed_ods.sql",
        } | set(self.contract["contracts"])
        for model in self.manifests.values():
            assets.update(model[key] for key in ("ddl", "transform", "sql") if model.get(key))
        self.asset_version = fingerprint({name: sha256((ROOT / name).read_bytes()).hexdigest()
                                          for name in sorted(assets)})
        self.access_version = fingerprint({
            name: [relation["schema_usage"], relation["metric_schema_usage"], {
                column: [spec["readable"], spec["metric_readable"]]
                for column, spec in relation["columns"].items()
            }]
            for name, relation in snapshot.relations.items()
        })
        self.version = fingerprint([self.asset_version, snapshot.schema_version, self.access_version])
        self.errors = self.validate_models()
        self.joins, self.invalid_joins = self.build_joins()
        self.standard = {mid: self.standard_contract(mid) for mid in self.service.registry}
        self.models = self.build_models()

    def validate_models(self):
        errors = {}
        for name, model in self.manifests.items():
            actual = self.snapshot.get(name)
            is_view = name.startswith("ads.")
            if not actual or actual["kind"] != ("v" if is_view else "r") or actual["rls"]:
                errors[name] = "对象缺失、类型不符或启用了未登记的行级安全。"
                continue
            columns = actual["columns"]
            expected = {column["name"]: column for column in model["columns"]}
            if columns.keys() != expected.keys() or any(
                columns[key]["type"] != spec["type"]
                or ("nullable" in spec and columns[key]["nullable"] != spec["nullable"])
                for key, spec in expected.items()
            ):
                errors[name] = "实际列名、类型或可空性与模型清单不一致。"
                continue
            if not is_view and (
                sorted(item["definition"] for item in actual["constraints"] if item["validated"])
                != sorted(model["constraints"])
                or any(not item["validated"] for item in actual["constraints"])
            ):
                errors[name] = "实际键约束与版本化模型不一致。"
            for field in self.contract["event_times"].get(name, {}).values():
                if columns.get(field, {}).get("type") not in TIMESTAMP_TYPES:
                    errors[name] = "业务时间字段缺失或类型不兼容。"
        return errors

    def build_joins(self):
        links = [dict(link, evidence="business_contract") for link in self.contract["logical_joins"]]
        for name in self.manifests:
            actual = self.snapshot.get(name)
            if not actual:
                continue
            for key in actual["constraints"]:
                if (key["kind"] == "f" and key["validated"] and len(key["columns"]) == 1
                        and key["target"] in self.manifests):
                    links.append({"from": name, "to": key["target"], "from_column": key["columns"][0],
                                  "to_column": key["target_columns"][0], "evidence": "postgresql_foreign_key"})
        accepted, rejected = {}, {}
        for link in links:
            origin, target, left, right = link_key(link)
            left_model, right_model = self.snapshot.get(origin), self.snapshot.get(target)
            left_column = left_model["columns"].get(left) if left_model else None
            right_column = right_model["columns"].get(right) if right_model else None
            if (origin in self.errors or target in self.errors or not left_column or not right_column
                    or left_column["type"] != right_column["type"]
                    or (right,) not in unique_keys(right_model)):
                rejected[link_key(link)] = "关联键缺失、类型不一致或目标键不唯一。"
                continue
            cardinality = "one_to_one" if (left,) in unique_keys(left_model) else "many_to_one"
            accepted[link_key(link)] = dict(link, relationship_cardinality=cardinality)
        return accepted, rejected

    def standard_contract(self, mid):
        metric = self.service.metrics[mid]
        spec = self.service.registry[mid]
        template = self.service.templates[mid]
        sources = list(dict.fromkeys(RELATIONS.findall(template)))
        axis = SHARE_AXES.get(mid)
        fields = {}
        for name in sources:
            if name not in self.manifests:
                raise ValueError(f"Undeclared source for {mid}: {name}")
            columns = [column["name"] for column in self.manifests[name]["columns"]]
            # Current templates select s.* from DWS; PostgreSQL requires access to every expanded column.
            fields[name] = columns if name.startswith("dws.") else [
                column for column in columns if re.search(rf"\b{re.escape(column)}\b", template)
            ]
        return {
            "source": metric["existing_summary"], "sources": sources, "fields": fields,
            "dimensions": [axis] if axis else spec["groups"],
            "event_time": TIME_BY_FAMILY.get(spec["family"], "order_time"),
            "definition": metric["definition"], "formula": metric["formula"],
            "observation_cutoff": self.service.cutoff.date().isoformat() if spec["family"] == "cohort" else None,
        }

    def build_models(self):
        models = {}
        for name, manifest in self.manifests.items():
            mappings = {mid: spec for mid, spec in self.standard.items() if name == spec["source"]}
            details = [spec for spec in self.contract["details"].values() if spec["source"] == name]
            registered = self.contract["model_fields"].get(name, {})
            columns = {column["name"] for column in manifest["columns"]}
            physical = set(registered.get("dimensions", [])) | set(registered.get("measures", []))
            if physical - columns:
                raise ValueError(f"Capability fields do not match versioned model: {name}")
            dimensions = {axis for spec in mappings.values() for axis in spec["dimensions"]}
            if mappings:
                dimensions.add("month")
            dimensions.update(registered.get("dimensions", []))
            dimensions.update(axis for spec in details for axis in spec["dimensions"])
            paths = [edge for edge in self.joins.values() if edge["from"] == name]
            models[name] = {
                "model_id": name, "physical_source": name,
                "business_grain": manifest.get("keys", [manifest["key"]]),
                "supported_dimensions": sorted(dimensions),
                "physical_measures": registered.get("measures", []),
                "measures": {mid: {"formula": spec["formula"], "definition": spec["definition"]}
                             for mid, spec in mappings.items() if spec["source"] == name},
                "event_time": self.contract["event_times"].get(name, {}),
                "join_paths": paths, "relationship_cardinality": [edge["relationship_cardinality"] for edge in paths],
                "aggregation_constraints": [
                    "保持声明粒度；比率从分子、分母重算，禁止直接合并不兼容事实。"
                ] + [rule for spec in details if spec["source"] == name for rule in spec["constraints"]],
                "quality_limitations": self.contract["quality"].get(name, []),
                "access_policy": "standard_metric" if name.startswith("dws.") else
                                 "detail_planning" if details else
                                 "approved_dimension_join" if name.startswith("dim.") else "candidate_only",
                "grain_evidence": "published_view_contract" if name.startswith("ads.") else "postgresql_primary_key",
                "metadata_version": self.version,
                "manifest": manifest["manifest"], "model_version": manifest["model_version"],
                "lineage": manifest.get("depends_on", []),
                "schema_error": self.errors.get(name),
            }
        return models

    def path(self, origin, target):
        queue = [(origin, [])]
        visited = {origin}
        for current, route in queue:
            if current == target:
                return route
            for edge in self.joins.values():
                following = edge["to"]
                if edge["from"] == current and following not in visited:
                    visited.add(following)
                    queue.append((following, route + [edge]))
        return None

    def readable(self, name, fields, *, standard):
        relation = self.snapshot.get(name)
        prefix = "metric_" if standard else ""
        return bool(relation and relation[prefix + "schema_usage"]
                    and all(relation["columns"].get(field, {}).get(prefix + "readable") for field in fields))
