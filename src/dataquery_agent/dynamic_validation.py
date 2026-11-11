"""Separate AST safety checks from operation-specific semantic proof."""
import json

from sqlglot import exp, parse, parse_one
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.qualify import qualify

from .dynamic_context import compile_blocks
from .sql_guard import validate_sql

FUNCTIONS = {exp.Count, exp.And, exp.RowNumber}
REPAIRABLE = {"invalid_model_output", "unknown_column", "sql_syntax"}


class CandidateError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def candidate_from_json(raw, context):
    try:
        candidate = json.loads(raw) if isinstance(raw, str) and len(raw) <= 8192 else None
    except (ValueError, RecursionError):
        candidate = None
    if not isinstance(candidate, dict) or set(candidate) != {
        "sql", "parameters", "sources", "output_fields", "steps", "unresolved",
    } or not isinstance(candidate["sql"], str):
        raise CandidateError("invalid_model_output")
    for name in ("sources", "output_fields", "steps", "unresolved"):
        value = candidate[name]
        if not isinstance(value, list) or len(value) > 16 or any(not isinstance(item, str) for item in value):
            raise CandidateError("invalid_model_output")
    if candidate["unresolved"]:
        raise CandidateError("unresolved_business_rules")
    params = candidate["parameters"]
    expected = context["parameters"]
    if (not isinstance(params, dict) or params.keys() != expected.keys()
            or any(type(params[key]) is not type(value) or params[key] != value for key, value in expected.items())):
        raise CandidateError("parameter_mismatch")
    if sorted(candidate["sources"]) != sorted(context["models"]) or candidate["output_fields"] != context["output_fields"]:
        raise CandidateError("declared_scope_mismatch")
    return candidate


class DynamicAstValidator:
    def validate(self, sql, context):
        try:
            statements = parse(sql, read="postgres")
        except (ParseError, TokenError, RecursionError):
            raise CandidateError("sql_syntax") from None
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise CandidateError("unsafe_sql")
        allowed, _ = validate_sql(sql, allowed_tables={tuple(name.split(".")) for name in context["models"]},
                                  allowed_functions=FUNCTIONS, exact_tables=True)
        ast = statements[0]
        if not allowed or ast.find(exp.Literal) or any(node.comments for node in ast.walk()):
            raise CandidateError("unsafe_sql")
        if {node.name for node in ast.find_all(exp.Placeholder)} != context["parameters"].keys():
            raise CandidateError("parameter_mismatch")
        return ast

    @staticmethod
    def qualified(ast, context):
        schema = {}
        for source, model in context["models"].items():
            namespace, name = source.split(".")
            schema.setdefault(namespace, {})[name] = model["fields"]
        try:
            return qualify(ast.copy(), dialect="postgres", schema=schema,
                           quote_identifiers=False, identify=False, validate_qualify_columns=True)
        except (OptimizeError, RecursionError):
            raise CandidateError("unknown_column") from None


class SemanticSqlValidator:
    def validate(self, actual, context, ast_validator):
        reference = parse_one(compile_blocks(context["blocks"]), read="postgres")
        actual = ast_validator.qualified(actual, context)
        reference = ast_validator.qualified(reference, context)
        for tree in (actual, reference):
            for ordered in tree.find_all(exp.Ordered):
                if not ordered.args.get("desc"):
                    ordered.set("desc", None)
        if actual != reference:
            raise CandidateError("semantic_mismatch")
        return actual
