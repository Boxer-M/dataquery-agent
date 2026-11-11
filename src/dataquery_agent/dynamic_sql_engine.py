"""Generate, independently prove, and execute approved dynamic analysis SQL."""
from datetime import datetime
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from dotenv import dotenv_values

from .agent_runtime import ModelUnavailable
from .capability_registry import CapabilityRegistry
from .capability_resolver import CapabilityResolver
from .detail_executor import authorize_detail, validated_rows
from .dynamic_context import PROMPT_VERSION, build_context, generation_prompt
from .dynamic_validation import CandidateError, DynamicAstValidator, REPAIRABLE, SemanticSqlValidator, candidate_from_json
from .metadata_service import MetadataService
from .metrics import ROOT
from .sql_executor import BoundedQueryError, execute_bounded_readonly

DYNAMIC_ROLE = "sqlshed_dynamic_reader"
MESSAGES = {
    "unsafe_sql": "生成的 SQL 超出只读安全范围。",
    "semantic_mismatch": "无法证明生成的 SQL 符合批准的关联、时间或计算口径。",
    "parameter_mismatch": "生成的参数与批准的请求不一致。",
    "unresolved_business_rules": "需要先明确业务条件。",
    "query_timeout": "动态分析查询超时。",
    "access_denied": "动态查询账号没有所需权限。",
    "schema_drift": "数据能力版本已变化，请重新提交需求。",
    "model_unavailable": "模型暂时不可用。",
}


class DynamicSqlEngine:
    def __init__(self, dsn, snapshot):
        self.dsn = dsn
        self.snapshot = snapshot
        self.ast = DynamicAstValidator()
        self.semantics = SemanticSqlValidator()

    @classmethod
    def local(cls):
        dsn = dotenv_values(ROOT / ".env.dynamic-query").get("DYNAMIC_QUERY_DSN")
        return cls(dsn, lambda: MetadataService.local().snapshot())

    def execute(self, question, requirement, runtime, *, agent_trace_id=None):
        began = monotonic()
        trace = {"trace_id": uuid4().hex, "agent_trace_id": agent_trace_id, "prompt_version": PROMPT_VERSION,
                 "generation_attempts": [], "sql_validated": False}
        try:
            result = self._execute(question, requirement, runtime, trace)
        except (CandidateError, BoundedQueryError) as error:
            status = "clarify" if error.code == "unresolved_business_rules" else (
                "unsupported" if error.code in {"unsafe_sql", "semantic_mismatch", "access_denied"} else "error")
            result = {"status": status, "code": error.code,
                      "message": MESSAGES.get(error.code, "动态查询未通过执行校验。")}
        except ModelUnavailable:
            result = {"status": "error", "code": "model_unavailable", "message": MESSAGES["model_unavailable"]}
        except (OSError, ValueError, KeyError, psycopg.Error):
            result = {"status": "error", "code": "metadata_unavailable", "message": "动态查询元数据暂不可用。"}
        trace.update(status=result["status"], error_code=result.get("code"),
                     elapsed_ms=round((monotonic() - began) * 1000))
        return {**result, "execution_trace": trace}

    def _execute(self, question, requirement, runtime, trace):
        snapshot = self.snapshot()
        registry = CapabilityRegistry(snapshot)
        route = CapabilityResolver(registry).resolve_dynamic(requirement)
        trace["capability"] = route
        if route["status"] != "DYNAMIC_QUERY":
            return {"status": "unsupported", "code": route["reason_code"], "message": route["reason"]}
        if not self.dsn:
            raise BoundedQueryError("dynamic_reader_not_configured")
        context = build_context(requirement, route, registry)
        trace["context_summary"] = {key: value for key, value in context.items() if key != "blocks"}
        statement = self.generate(question, context, runtime, trace)
        current = self.snapshot()
        refreshed = CapabilityRegistry(current)
        if (refreshed.version != registry.version or CapabilityResolver(refreshed).resolve_dynamic(
                requirement).get("capability_version") != route["capability_version"]):
            raise BoundedQueryError("schema_drift")
        params = dict(context["parameters"])
        for field in ("start", "end"):
            params[field] = datetime.fromisoformat(params[field])
        result = execute_bounded_readonly(
            self.dsn, statement, params, role=DYNAMIC_ROLE,
            validate=lambda value: self.validate(value, context),
            authorize=lambda pg: authorize_detail(pg, current, route["required_fields"]),
        )
        rows = validated_rows(SimpleNamespace(result_schema=tuple(route["result_schema"])), result, grain=route["grain"])
        self.validate_results(rows, requirement)
        return {
            "status": "ok", "operation": requirement.operation, "question": question,
            "path": "dynamic_sql", "definition": route["definition"], "event_time": route["event_time"],
            "grain": route["grain"], "sources": list(context["models"]), "parameters": context["parameters"],
            "metadata_version": registry.version, "capability_version": route["capability_version"],
            "prompt_version": PROMPT_VERSION, "sql": statement, "results": rows, "truncated": result.truncated,
            "quality_limitations": route["risks"],
        }

    def validate(self, statement, context):
        tree = self.ast.validate(statement, context)
        return self.semantics.validate(tree, context, self.ast)

    def generate(self, question, context, runtime, trace):
        repair = None
        for index in (1, 2):
            attempt = {"attempt": index}
            trace["generation_attempts"].append(attempt)
            try:
                raw = runtime.call("dynamic_sql", generation_prompt(question, context, repair))
                attempt["model_output"] = raw[:8192] if isinstance(raw, str) else None
                proposal = candidate_from_json(raw, context)
                tree = self.validate(proposal["sql"], context)
            except CandidateError as error:
                attempt.update(status="rejected", code=error.code)
                if index == 2 or error.code not in REPAIRABLE:
                    raise
                repair = error.code
            except ModelUnavailable:
                attempt.update(status="error", code="model_unavailable")
                raise
            else:
                attempt["status"] = "validated"
                trace["sql_validated"] = True
                trace["bound_parameters"] = context["parameters"]
                return tree.sql(dialect="postgres", comments=False)
        raise CandidateError("invalid_model_output")

    @staticmethod
    def validate_results(rows, requirement):
        by_channel = {}
        for row in rows:
            if requirement.operation == "attempts_by_order" and row["attempt_count"] <= requirement.threshold:
                raise BoundedQueryError("invalid_result")
            if requirement.operation == "attempt_rank_by_channel":
                position = by_channel.get(row["channel"], 0) + 1
                if row["rank"] != position or position > requirement.top_n or row["attempt_count"] < 1:
                    raise BoundedQueryError("invalid_result")
                by_channel[row["channel"]] = position
            if requirement.operation == "return_orders_by_category" and row["order_count"] < 1:
                raise BoundedQueryError("invalid_result")


def main():
    from .unified_agent import main as agent_main

    return agent_main()


if __name__ == "__main__":
    raise SystemExit(main())
