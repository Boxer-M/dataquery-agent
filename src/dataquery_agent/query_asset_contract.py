"""The initial publishable asset uses the existing channel ranking business contract."""
from dataclasses import replace
from hashlib import sha256

from sqlglot import exp, parse_one

from .capability_registry import CapabilityRegistry
from .capability_resolver import CapabilityResolver
from .dynamic_context import build_context
from .dynamic_requirements import COVERAGE, DynamicRequirement, ROW_LIMIT, approved_spec
from .metadata_service import fingerprint, manifest_models
from .metrics import ROOT
from .query_asset_store import AssetError

OPERATION = "attempt_rank_by_channel"
PARAMETERS = {
    "start": {"type": "date", "required": True, "minimum": COVERAGE[0].isoformat(), "inclusive": True},
    "end": {"type": "date", "required": True, "maximum": COVERAGE[1].isoformat(), "exclusive": True},
    "top_n": {"type": "integer", "required": True, "minimum": 1, "maximum": 20},
    "row_limit": {"type": "integer", "required": False, "default": ROW_LIMIT, "constant": ROW_LIMIT},
}
RULES = {
    "object": "one payment attempt, including unsuccessful attempts",
    "deduplication": "no attempt deduplication; group by order and its channel",
    "filter": "paid_at >= start AND paid_at < end; all attempt statuses",
    "time_basis": "source timestamp without timezone; date boundaries at source midnight",
    "aggregation": "count attempts, per-channel row number by count DESC, order_id ASC",
    "output_order": "channel ASC, rank ASC",
    "dimensions": ["channel"],
    "limits": "1 <= top_n <= 20, bounded output with explicit truncation",
}
EXAMPLE = DynamicRequirement(OPERATION, "2025-01-01", "2025-02-01", top_n=3)
CTE_ALIASES = {"asset_grouped": "grouped", "asset_ranked": "ranked"}


def contract(requirement=EXAMPLE):
    version, spec = approved_spec(requirement)
    return {"semantic_contract_id": "dynamic/" + requirement.operation, "semantic_contract_version": version,
            "specification": spec, "business_rules": RULES, "parameter_schema": PARAMETERS}


def canonical_sql(statement):
    if not isinstance(statement, str) or not 1 <= len(statement) <= 8192:
        raise AssetError("invalid_asset_sql")
    from sqlglot import parse
    from sqlglot.errors import SqlglotError
    try:
        statements = parse(statement, read="postgres")
    except (SqlglotError, RecursionError):
        raise AssetError("invalid_asset_sql") from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise AssetError("invalid_asset_sql")
    tree = statements[0]
    if tree.find(exp.Literal) or any(node.comments for node in tree.walk()):
        raise AssetError("asset_requires_parameters")
    return tree.sql(dialect="postgres")


def semantic_sql(statement):
    """Allow only two explicit, equivalent CTE aliases before the existing exact AST proof."""
    tree = parse_one(statement, read="postgres")
    aliases = {cte.alias for cte in tree.find_all(exp.CTE)}
    for old, new in CTE_ALIASES.items():
        if old in aliases and new not in aliases:
            for cte in tree.find_all(exp.CTE):
                if cte.alias == old:
                    cte.args["alias"].set("this", exp.to_identifier(new))
            for table in tree.find_all(exp.Table):
                if table.name == old and not table.db:
                    table.set("this", exp.to_identifier(new))
    return tree.sql(dialect="postgres")


def dependencies(snapshot, business):
    """Project only the declared physical dependencies. Extra columns have no effect."""
    from json import loads
    capability = loads((ROOT / "warehouse/capability_contract.json").read_text())
    models = manifest_models()
    spec = business["specification"]
    physical = {}
    for name, fields in spec["fields"].items():
        actual = snapshot.get(name)
        model = models[name]
        physical[name] = None if not actual else {
            "kind": actual["kind"], "rls": actual["rls"], "constraints": actual["constraints"],
            "fields": {field: {key: actual["columns"][field][key] for key in ("type", "nullable")}
                       if field in actual["columns"] else None for field in fields},
            "grain": model.get("keys", [model["key"]]),
            "model_version": model["model_version"],
            "implementation": {key: sha256((ROOT / model[key]).read_bytes()).hexdigest()
                               for key in ("ddl", "transform") if model.get(key)},
        }
    relevant = {tuple(edge) for edge in spec["joins"]}
    declared = [link for link in capability["logical_joins"]
                if tuple(link[key] for key in ("from", "to", "from_column", "to_column")) in relevant]
    return {"models": physical, "join_dependencies": spec["joins"], "logical_joins": declared,
            "event_time": capability["event_times"].get(spec["source"], {}).get(spec["event_time"])}


def analyze_asset_impact(definition, snapshot):
    business = contract()
    current = dependencies(snapshot, business)
    previous = definition["dependencies"]
    findings = []
    if definition["semantic_hash"] != fingerprint(business) or previous["event_time"] != current["event_time"]:
        findings.append(["business_uncertain", "business_contract_or_event_time"])
    if (previous["join_dependencies"], previous["logical_joins"]) != (current["join_dependencies"], current["logical_joins"]):
        findings.append(["incompatible", "join_contract"])
    for name, before in previous["models"].items():
        after = current["models"].get(name)
        if not before or not after:
            findings.append(["incompatible", name])
            continue
        for field, expected in before["fields"].items():
            if expected is None or expected != after["fields"].get(field):
                findings.append(["incompatible", name + "." + field])
        for key in ("kind", "rls", "constraints"):
            if before[key] != after[key]:
                findings.append(["incompatible", name + "." + key])
        if before["grain"] != after["grain"]:
            findings.append(["business_uncertain", name + ".grain"])
        if any(before[key] != after[key] for key in ("model_version", "implementation")):
            findings.append(["compatible_requires_validation", name + ".model_implementation"])
    priority = ("incompatible", "business_uncertain", "compatible_requires_validation")
    return {"classification": next((kind for kind in priority if any(row[0] == kind for row in findings)), "unrelated"),
            "findings": findings, "metadata_fingerprint": fingerprint(current)}


def asset_context(requirement, snapshot):
    # CapabilityRegistry checks full model manifests. Scope away extra columns only;
    # keep every original type, constraint and policy check for contracted columns.
    models = manifest_models()
    scoped = {name: {**relation, "columns": {
        key: spec for key, spec in relation["columns"].items()
        if key in {column["name"] for column in models[name]["columns"]}}}
        if name in models else relation for name, relation in snapshot.relations.items()}
    registry = CapabilityRegistry(replace(snapshot, relations=scoped))
    route = CapabilityResolver(registry).resolve_dynamic(requirement)
    if route["status"] != "DYNAMIC_QUERY":
        raise AssetError(route["reason_code"])
    return route, build_context(requirement, route, registry)
