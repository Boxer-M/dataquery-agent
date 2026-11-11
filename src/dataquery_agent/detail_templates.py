"""Versioned, immutable SQL assets for approved detail analyses."""
import json
import re
from dataclasses import dataclass
from hashlib import sha256

from sqlglot import exp

from .metrics import ROOT
from .sql_executor import BoundedQueryError
from .sql_guard import ALLOWED_FUNCTION_NODES, validate_sql

CONTRACT = ROOT / "warehouse/detail_query_contract.json"
DETAIL_ROLE = "sqlshed_detail_reader"
DETAIL_FUNCTIONS = ALLOWED_FUNCTION_NODES | {exp.And, exp.Exists}


@dataclass(frozen=True)
class DetailTemplate:
    template_id: str
    template_version: str
    statement: str
    source_models: tuple
    result_schema: tuple
    description: str

    def validate_sql(self, statement):
        if statement != self.statement or set(re.findall(r"%\(([a-z_]+)\)s", statement)) != {"start", "end"}:
            raise BoundedQueryError("template_sql_mismatch")
        tables = {tuple(name.split(".")) for name in self.source_models}
        allowed, _ = validate_sql(statement, allowed_tables=tables, allowed_functions=DETAIL_FUNCTIONS,
                                  exact_tables=True)
        if not allowed or any(not name.startswith(("dwd.", "dim.")) for name in self.source_models):
            raise BoundedQueryError("unsafe_template")


class DetailTemplateRegistry:
    def __init__(self, path=CONTRACT):
        self.contract = json.loads(path.read_text())

    def supports(self, analysis_id, groups=None):
        analysis = self.contract["analyses"].get(analysis_id) if isinstance(analysis_id, str) else None
        return analysis is not None and (groups is None or any(
            sorted(item["dimensions"]) == sorted(groups) for item in analysis["templates"]))

    def load(self, analysis_id, groups, version=None):
        analysis = self.contract["analyses"].get(analysis_id)
        if analysis is None:
            raise BoundedQueryError("template_not_registered")
        spec = next((item for item in analysis["templates"] if sorted(item["dimensions"]) == sorted(groups)), None)
        if spec is None:
            raise BoundedQueryError("unsupported_grouping")
        if version is not None and spec["template_version"] != version:
            raise BoundedQueryError("template_version_mismatch")
        path = (ROOT / spec["path"]).resolve()
        if not path.is_relative_to(ROOT / "sql/warehouse/detail"):
            raise BoundedQueryError("unsafe_template")
        content = path.read_bytes()
        if sha256(content).hexdigest() != spec["sha256"]:
            raise BoundedQueryError("template_version_mismatch")
        template = DetailTemplate(spec["template_id"], spec["template_version"], content.decode().strip(),
                                  tuple(spec["source_models"]), tuple(spec["result_schema"].items()),
                                  analysis["business_description"])
        template.validate_sql(template.statement)
        return template
