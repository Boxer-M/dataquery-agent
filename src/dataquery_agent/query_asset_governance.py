"""Propose, verify and publish immutable implementations of an approved analysis."""
from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic
from uuid import uuid4

import psycopg

from .capability_registry import CapabilityRegistry
from .capability_resolver import CapabilityResolver
from .conversation_store import identifier
from .dynamic_requirements import DynamicRequirement, approved_spec
from .dynamic_sql_engine import DynamicSqlEngine
from .dynamic_validation import CandidateError
from .metadata_service import MetadataService, fingerprint
from .query_asset_contract import (EXAMPLE, OPERATION, PARAMETERS, analyze_asset_impact, asset_context,
                                   canonical_sql, contract, dependencies)
from .query_asset_store import AssetError, QueryAssetStore
from .sql_executor import BoundedQueryError

VALIDATION_VERSION = "channel-top-ods-v1"


def reference_rows(payments, top_n):
    """ODS grouped counts, ranked independently in Python with deterministic ties."""
    channels = {}
    for channel, order_id, count in payments:
        channels.setdefault(channel, []).append((order_id, count))
    return [{"channel": channel, "order_id": order_id, "attempt_count": count, "rank": rank}
            for channel, orders in sorted(channels.items())
            for rank, (order_id, count) in enumerate(sorted(orders, key=lambda item: (-item[1], item[0]))[:top_n], 1)]


def ods_truth(requirement):
    cfg = MetadataService.local().config
    with psycopg.connect(host=cfg["SQLSHED_HOST"], port=cfg["SQLSHED_PORT"], dbname="sqlshed",
                         user=cfg["SQLSHED_USER"], password=cfg["SQLSHED_PASSWORD"], connect_timeout=5) as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        pg.execute("SET LOCAL statement_timeout = '10s'")
        counts = pg.execute("""
            SELECT o.channel, p.order_id, COUNT(*)
            FROM ods.payments p JOIN ods.orders o USING (order_id)
            WHERE p.paid_at >= %s AND p.paid_at < %s
            GROUP BY o.channel, p.order_id
        """, (requirement.start, requirement.end)).fetchall()
    output = reference_rows(counts, requirement.top_n)
    return output[:100], len(output) > 100


class QueryAssetGovernance:
    def __init__(self, store, engine, truth=ods_truth):
        self.store = store
        self.engine = engine
        self.truth = truth

    def propose(self, statement, provenance):
        business = contract()
        snapshot = self.engine.snapshot()
        physical = dependencies(snapshot, business)
        spec = business["specification"]
        statement = canonical_sql(statement)
        return self.store.create({
            "asset_id": OPERATION, "asset_type": "analysis_template", "operation": OPERATION, **business,
            "canonical_sql": statement, "sql_hash": sha256(statement.encode()).hexdigest(),
            "semantic_hash": fingerprint(business), "dependencies": physical,
            "metadata_fingerprint": fingerprint(physical), "source_models": sorted(spec["fields"]),
            "field_dependencies": spec["fields"], "join_dependencies": spec["joins"],
            "time_semantics": [spec["event_time"], spec["time_column"], "[start,end)"],
            "grain": spec["grain"], "output_schema": spec["result_schema"], "provenance": provenance,
        })

    def from_history(self, conversations, owner, query_id):
        history = conversations.history(owner, query_id)
        if (history.get("execution_strategy") != "dynamic_sql" or history.get("execution_status") != "ok"
                or history.get("analysis_id") != OPERATION or not history.get("sql")):
            raise AssetError("ineligible_history")
        params = history["parameter_summary"]
        if not isinstance(params, dict) or params.keys() != PARAMETERS.keys():
            raise AssetError("ineligible_history")
        req = DynamicRequirement(OPERATION, params["start"], params["end"], top_n=params["top_n"])
        approved_spec(req)
        if req.parameters() != params:
            raise AssetError("ineligible_history")
        return self.propose(history["sql"], {"type": "query_history", "source_query_id": str(identifier(query_id))})

    def from_detail(self, analysis, groups):
        from .detail_templates import DetailTemplateRegistry
        registry = DetailTemplateRegistry()
        template = registry.load(analysis, groups)
        metadata = CapabilityRegistry(self.engine.snapshot())
        route = CapabilityResolver(metadata).resolve({
            "analysis_id": analysis, "start_date": EXAMPLE.start, "end_date": EXAMPLE.end, "group_by": groups,
        })
        if route["status"] != "DETAIL_QUERY":
            raise AssetError(route["reason_code"])
        business = registry.contract["analyses"][analysis]
        if set(route["required_fields"]) != set(template.source_models):
            raise AssetError("capability_mismatch")
        asset_id = "detail/" + template.template_id
        time_semantics = {key: value for key, value in route["time_constraints"].items()
                          if key not in {"start_date", "end_date"}}
        return self.store.create({
            "asset_id": asset_id, "asset_type": "detail_template", "operation": asset_id,
            "semantic_contract_id": analysis, "semantic_contract_version": registry.contract["version"],
            "semantic_hash": fingerprint([business, metadata.contract["details"][analysis]]),
            "canonical_sql": template.statement, "sql_hash": sha256(template.statement.encode()).hexdigest(),
            "parameter_schema": {key: {"type": "date", "required": True} for key in ("start", "end")},
            "output_schema": template.result_schema, "source_models": list(template.source_models),
            "business_rules": metadata.contract["details"][analysis]["constraints"],
            "field_dependencies": route["required_fields"], "join_dependencies": route["join_paths"],
            "time_semantics": time_semantics, "grain": route["grain"], "metadata_fingerprint": metadata.version,
            "provenance": {"type": "detail_contract", "version": template.template_version},
        })

    @staticmethod
    def require_current(definition, snapshot):
        report = analyze_asset_impact(definition, snapshot)
        if report["classification"] != "unrelated" or report["metadata_fingerprint"] != definition["metadata_fingerprint"]:
            raise AssetError("asset_metadata_stale")
        return report

    def verify(self, asset_id, version):
        row = self.store.read(asset_id, version)
        definition = row["definition"]
        if row["lifecycle_status"] != "CANDIDATE" or definition["operation"] != OPERATION:
            raise AssetError("unsupported_validation")
        snapshot = self.engine.snapshot()
        self.require_current(definition, snapshot)
        cases = []
        try:
            for start, end, top in ((EXAMPLE.start, EXAMPLE.end, 3), ("2025-02-01", "2025-03-01", 1),
                                    ("2025-01-01", "2025-01-02", 20), ("2024-01-01", "2024-01-02", 2)):
                req = DynamicRequirement(OPERATION, start, end, top_n=top)
                route, context = asset_context(req, snapshot)
                result = self.engine.execute_approved(definition["canonical_sql"], req, route, context, snapshot,
                                                      asset=True)
                expected, truncated = self.truth(req)
                equal = result["results"] == expected and result["truncated"] == truncated
                cases.append({"parameters": req.parameters(), "passed": equal, "row_count": len(expected),
                              "truncated": truncated, "expected_hash": fingerprint(expected),
                              "actual_hash": fingerprint(result["results"])})
                if not equal:
                    raise AssetError("ground_truth_mismatch")
        except (AssetError, CandidateError, BoundedQueryError) as error:
            return self.store.transition(asset_id, version, "REJECTED",
                                         evidence={"code": str(error), "cases": cases})
        proof = {"status": "passed", "version": VALIDATION_VERSION, "cases": cases,
                 "sql_hash": definition["sql_hash"], "semantic_hash": definition["semantic_hash"],
                 "metadata_fingerprint": definition["metadata_fingerprint"],
                 "validated_at": datetime.now(timezone.utc).isoformat()}
        return self.store.transition(asset_id, version, "VALIDATED", evidence=proof,
                                     check=lambda item: self.require_current(item["definition"], self.engine.snapshot()))

    @staticmethod
    def require_proof(row):
        definition = row["definition"]
        proof = row["validation_evidence"] or {}
        if (definition["operation"] != OPERATION or proof.get("status") != "passed"
                or proof.get("version") != VALIDATION_VERSION or len(proof.get("cases", [])) != 4
                or not all(case["passed"] for case in proof["cases"])
                or definition["sql_hash"] != sha256(definition["canonical_sql"].encode()).hexdigest()
                or any(proof.get(key) != definition[key] for key in
                       ("sql_hash", "semantic_hash", "metadata_fingerprint"))):
            raise AssetError("validation_required")

    def publish(self, asset_id, version):
        def check(row):
            self.require_proof(row)
            self.require_current(row["definition"], self.engine.snapshot())
        return self.store.transition(asset_id, version, "PUBLISHED", check=check)

    def impact(self, asset_id, version, *, mark=False):
        row = self.store.read(asset_id, version)
        if row["operation"] != OPERATION:
            raise AssetError("unsupported_impact")
        report = analyze_asset_impact(row["definition"], self.engine.snapshot())
        if mark and row["lifecycle_status"] == "PUBLISHED" and report["classification"] != "unrelated":
            self.store.transition(asset_id, version, "STALE", evidence=report)
            row["lifecycle_status"] = "STALE"
        return {**report, "lifecycle_status": row["lifecycle_status"]}


class QueryAssetRuntime:
    def __init__(self, store, engine):
        self.store = store
        self.engine = engine

    @classmethod
    def local(cls, engine):
        from .metrics import ROOT
        return cls(QueryAssetStore.local(), engine) if (ROOT / ".env.query-assets").exists() else None

    def execute(self, question, requirement, *, agent_trace_id):
        if requirement.operation != OPERATION:
            return None
        trace = {"trace_id": uuid4().hex, "agent_trace_id": agent_trace_id, "generation_attempts": [],
                 "execution_strategy": "published_asset", "sql_validated": False}
        started = monotonic()
        try:
            with self.store.active(requirement.operation) as row:
                if row is None:
                    return None
                definition = row["definition"]
                version = {key: definition[key] for key in
                           ("semantic_contract_version", "sql_hash", "metadata_fingerprint")}
                version.update(asset_id=row["asset_id"], implementation_version=row["implementation_version"],
                               validation_status="unconfirmed")
                trace["query_asset"] = version
                if row["lifecycle_status"] != "PUBLISHED":
                    raise AssetError("asset_metadata_stale")
                QueryAssetGovernance.require_proof(row)
                version["validation_status"] = "passed"
                snapshot = self.engine.snapshot()
                report = analyze_asset_impact(definition, snapshot)
                trace["impact"] = report
                QueryAssetGovernance.require_current(definition, snapshot)
                route, context = asset_context(requirement, snapshot)
                trace["capability"] = route
                result = self.engine.execute_approved(definition["canonical_sql"], requirement, route, context,
                                                      snapshot, asset=True)
                result.update(question=question, path="published_asset", query_asset=version)
                trace.update(sql_validated=True, bound_parameters=context["parameters"])
        except (AssetError, CandidateError, BoundedQueryError, ValueError) as error:
            code = getattr(error, "code", None) or str(error)
            if code not in {"asset_metadata_stale", "access_denied", "schema_drift", "query_timeout",
                            "invalid_window", "outside_verified_window", "invalid_parameters"}:
                code = "asset_unavailable"
            result = {"status": "error", "code": code, "message": "已登记查询资产未通过当前执行检查。"}
        except (psycopg.Error, OSError, KeyError):
            result = {"status": "error", "code": "asset_unavailable", "message": "查询资产或元数据暂不可用。"}
        if "query_asset" in trace:
            result.update(path="published_asset", query_asset=trace["query_asset"])
        trace.update(status=result["status"], error_code=result.get("code"),
                     elapsed_ms=round((monotonic() - started) * 1000))
        return {**result, "execution_trace": trace}
