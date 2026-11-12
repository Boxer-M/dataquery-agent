"""Revalidate detail decisions and execute pinned, parameterized warehouse templates."""
from datetime import datetime
from decimal import Decimal
from time import monotonic
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row

from .capability_registry import CONTRACT as CAPABILITY_CONTRACT, CapabilityRegistry
from .capability_resolver import CapabilityResolver
from .detail_templates import DETAIL_ROLE, DetailTemplateRegistry
from .metadata_service import CATALOG_SQL, MetadataService, fingerprint
from .metric_query_service import ROLE as METRIC_ROLE
from .metrics import ROOT, read_queries
from .sql_executor import BoundedQueryError, execute_bounded_readonly

ERRORS = {
    "template_not_registered": "该明细需求尚无受控执行模板。",
    "detail_not_configured": "明细只读账号尚未配置。",
    "capability_mismatch": "明细需求或能力结论与已登记路径不一致。",
    "schema_drift": "模型结构或能力版本已变化，请刷新元数据。",
    "access_denied": "明细账号缺少读取权限或拥有超出契约的权限。",
    "query_timeout": "明细查询超时。",
    "plan_cost_limit": "查询估算成本超过限制，请缩小时间范围。",
    "invalid_result": "明细查询结果不符合已登记的字段、类型或粒度。",
    "template_version_mismatch": "SQL 模板版本或内容校验失败。",
    "capability_contract_mismatch": "明细模板尚未核准当前版本的业务规则。",
    "invalid_parameters": "明细参数不符合契约。",
}


def authorize_detail(connection, snapshot, fields):
    role_safe = connection.execute("""
        SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)
               AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid)
               AND NOT has_schema_privilege('ods', 'USAGE')
               AND NOT has_database_privilege(current_database(), 'CREATE,TEMP')
               AND NOT has_schema_privilege('dwd', 'CREATE')
               AND NOT has_schema_privilege('dim', 'CREATE')
               AND NOT has_schema_privilege('public', 'CREATE')
        FROM pg_roles r WHERE rolname = current_user
    """).fetchone()
    if role_safe != (True,):
        raise BoundedQueryError("access_denied")
    for name, columns in fields.items():
        privileges = connection.execute("""
            SELECT c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                   OR has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege(c.oid, 'INSERT,UPDATE,REFERENCES')
            FROM pg_class c WHERE c.oid = to_regclass(%s)
        """, (name,)).fetchone()
        if privileges is None:
            raise BoundedQueryError("schema_drift")
        if privileges != (False,):
            raise BoundedQueryError("access_denied")
        # Resolves every required column and holds a relation lock through execution.
        connection.execute(sql.SQL("SELECT {} FROM {} WHERE false").format(
            sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(*name.split("."))))
    params = {"names": sorted(fields), "metric_role": METRIC_ROLE}
    queries = read_queries(ROOT / CATALOG_SQL)
    with connection.cursor(row_factory=dict_row) as cursor:
        relations = {row["name"]: row for row in cursor.execute(queries["relations"], params)}
        columns = {}
        for row in cursor.execute(queries["columns"], params):
            columns.setdefault(row["name"], {})[row["column"]] = (row["type"], row["nullable"])
        constraints = {}
        for row in cursor.execute(queries["constraints"], params):
            constraints.setdefault(row.pop("name"), []).append(row)
    for name in fields:
        expected = snapshot.get(name)
        live = relations.get(name)
        if (not live or (live["kind"], live["rls"]) != (expected["kind"], expected["rls"])
                or columns.get(name) != {column: (item["type"], item["nullable"])
                                         for column, item in expected["columns"].items()}
                or constraints.get(name, []) != expected["constraints"]):
            raise BoundedQueryError("schema_drift")


def validated_rows(template, result, *, grain=None):
    columns = tuple(name for name, _ in template.result_schema)
    if columns != result.columns or ("order_count" == columns[0] and len(result.rows) != 1):
        raise BoundedQueryError("invalid_result")
    types = {"text": str, "integer": int, "decimal": Decimal}
    key_indexes = [columns.index(name) for name in (columns[:-1] if grain is None else grain)]
    seen = set()
    rows = []
    for row in result.rows:
        if len(row) != len(columns) or any(
            type(value) is not types[kind] or (kind == "decimal" and not value.is_finite())
            for value, (_, kind) in zip(row, template.result_schema)
        ):
            raise BoundedQueryError("invalid_result")
        key = tuple(row[index] for index in key_indexes)
        if key in seen or (columns[-1] == "order_count" and row[-1] < 0):
            raise BoundedQueryError("invalid_result")
        seen.add(key)
        rows.append({name: format(value, "f") if type(value) is Decimal else value
                     for name, value in zip(columns, row)})
    return rows


class DetailQueryExecutor:
    def __init__(self, dsn, snapshot, templates=None):
        self.dsn = dsn
        self.snapshot = snapshot
        self.templates = templates if templates is not None else DetailTemplateRegistry()

    @classmethod
    def local(cls):
        dsn = dotenv_values(ROOT / ".env.detail-query").get("DETAIL_QUERY_DSN")
        return cls(dsn, lambda: MetadataService.local().snapshot())

    def supports(self, analysis_id, groups=None):
        return self.templates.supports(analysis_id, groups)

    def execute_detail(self, analysis_id, parameters, capability_decision, trace_context):
        started = monotonic()
        trace = {"trace_id": uuid4().hex, "agent_trace_id": trace_context.get("trace_id") if isinstance(trace_context, dict) else None,
                 "analysis_id": analysis_id, "capability_trace_id": capability_decision.get("trace_id") if isinstance(capability_decision, dict) else None}
        try:
            result = self._execute(analysis_id, parameters, capability_decision, trace)
            trace.update(status="ok", error_code=None)
        except BoundedQueryError as error:
            trace.update(status="error", error_code=error.code)
            result = {"status": "error", "code": error.code,
                      "message": ERRORS.get(error.code, "受控明细查询未通过执行校验。")}
        except (psycopg.Error, OSError, KeyError, ValueError):
            trace.update(status="error", error_code="metadata_unavailable")
            result = {"status": "error", "code": "metadata_unavailable", "message": "无法核验明细元数据。"}
        trace["elapsed_ms"] = round((monotonic() - started) * 1000)
        return dict(result, analysis_id=analysis_id, execution_trace=trace)

    def _execute(self, analysis_id, parameters, decision, trace):
        if not self.supports(analysis_id):
            raise BoundedQueryError("template_not_registered")
        if not isinstance(decision, dict) or decision.get("status") != "DETAIL_QUERY" or decision.get("analysis_id") != analysis_id:
            raise BoundedQueryError("capability_mismatch")
        contract = self.templates.contract
        required = set(contract["required_parameters"])
        allowed = required | set(contract["optional_parameters"])
        if not isinstance(parameters, dict) or parameters.keys() - allowed or not required <= parameters.keys():
            raise BoundedQueryError("invalid_parameters")
        groups = parameters.get("group_by", [])
        version = parameters.get("template_version")
        if (not isinstance(groups, list) or any(not isinstance(axis, str) for axis in groups)
                or len(set(groups)) != len(groups) or (version is not None and not isinstance(version, str))):
            raise BoundedQueryError("invalid_parameters")
        for field in ("start_date", "end_date"):
            if not isinstance(parameters[field], str) or parameters[field] != decision.get("time_constraints", {}).get(field):
                raise BoundedQueryError("invalid_parameters")
        if groups != decision.get("dimensions"):
            raise BoundedQueryError("invalid_parameters")
        template = self.templates.load(analysis_id, groups, version)
        demand = {"analysis_id": analysis_id, **{key: value for key, value in parameters.items()
                                               if key != "template_version"}}
        trace.update(template_id=template.template_id, template_version=template.template_version,
                     sources=list(template.source_models), parameters={key: value for key, value in demand.items()
                                                                        if key != "analysis_id"})
        snapshot = self.snapshot()
        registry = CapabilityRegistry(snapshot)
        if registry.version != decision.get("metadata_version"):
            raise BoundedQueryError("schema_drift")
        approval = contract["analyses"][analysis_id]
        if (contract["capability_contract"] != CAPABILITY_CONTRACT
                or approval["capability_reference"] != analysis_id
                or approval["capability_fingerprint"] != fingerprint(registry.contract["details"].get(analysis_id))):
            raise BoundedQueryError("capability_contract_mismatch")
        current = CapabilityResolver(registry).resolve(demand)
        keys = ("status", "selected_source", "required_fields", "join_paths", "time_constraints", "grain")
        if any(current.get(key) != decision.get(key) for key in keys):
            raise BoundedQueryError("capability_mismatch")
        fields = current["required_fields"]
        if set(fields) != set(template.source_models):
            raise BoundedQueryError("template_sql_mismatch")
        if not self.dsn:
            raise BoundedQueryError("detail_not_configured")
        args = {"start": datetime.fromisoformat(parameters["start_date"]),
                "end": datetime.fromisoformat(parameters["end_date"])}
        result = execute_bounded_readonly(self.dsn, template.statement, args, role=DETAIL_ROLE,
                                          validate=template.validate_sql,
                                          authorize=lambda pg: authorize_detail(pg, snapshot, fields))
        rows = validated_rows(template, result)
        return {
            "status": "ok", "results": rows, "truncated": result.truncated,
            "definition": template.description, "time_constraints": current["time_constraints"],
            "grain": current["grain"], "constraints": current["aggregation_constraints"],
            "quality_limitations": current["risks"], "sources": list(template.source_models),
            "template_id": template.template_id, "template_version": template.template_version,
            "sql": template.statement,
        }
