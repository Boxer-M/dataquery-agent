"""Structured read-only access to the verified DWS metric templates."""
import argparse
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from time import monotonic
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .dws import REGISTRY, metric_query, models
from .metrics import ROOT, read_queries

ROLE = "sqlshed_metric_service"
TIMEOUT_MS = 5000
ROW_LIMIT = 1000
LOG = logging.getLogger(__name__)
CATALOG = ROOT / "metrics/warehouse_catalog_v1.json"
TEMPLATES = ROOT / "sql/warehouse/dws_metrics/dws.sql"
FIELDS = {"metric_id", "start_date", "end_date", "group_by", "filters", "observation_cutoff"}
SHARE_AXES = {"channel_order_share": "channel", "category_sales_share": "category"}
CATEGORY_FAMILIES = {"items", "returns", "cohort"}


class RequestError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise RequestError("invalid_date", "Use YYYY-MM-DD dates.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise RequestError("invalid_date", "Use valid calendar dates.") from None


def windows(start, end, monthly):
    while start < end:
        stop = end
        if monthly and (start.year, start.month) != (end.year, end.month):
            stop = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        yield start, stop
        start = stop


def json_value(value):
    return format(value, "f") if isinstance(value, Decimal) else value


class MetricQueryService:
    """Accept a dictionary and return a JSON-serializable response."""

    def __init__(self, dsn):
        self.dsn = dsn
        self.catalog = json.loads(CATALOG.read_text())
        self.metrics = {m["metric_id"]: m for m in self.catalog["metrics"]}
        self.registry = {m["metric_id"]: m for m in json.loads(REGISTRY.read_text())["metrics"]}
        self.templates = read_queries(TEMPLATES)
        self.cutoff = datetime.fromisoformat(models()["as_of"])
        verified = {mid for mid, m in self.metrics.items()
                    if m.get("dws_validation", {}).get("status") == "available"
                    and m["definition_status"] in {"available", "approved_v1_unchanged"}
                    and m.get("event_time") and m.get("ground_truth", {}).get("evidence")}
        if verified != self.registry.keys() or verified != self.templates.keys():
            raise ValueError("Verified metric registry and SQL templates disagree.")

    def validate(self, request):
        if not isinstance(request, dict) or request.keys() - FIELDS:
            raise RequestError("invalid_request", "Expected only documented request fields.")
        mid = request.get("metric_id")
        if not isinstance(mid, str) or mid not in self.metrics:
            raise RequestError("unknown_metric", "Metric ID is absent from the catalog.")
        metric = self.metrics[mid]
        if mid not in self.registry:
            status = metric["definition_status"]
            code = status if status in {"needs_definition", "unsupported"} else "metric_unverified"
            reason = metric.get("deferred_reason") or metric.get("data_quality_limits") or metric["definition"]
            raise RequestError(code, reason)
        spec = self.registry[mid]
        start, end = map(parse_date, (request.get("start_date"), request.get("end_date")))
        if not 0 < (end - start).days <= 1827:
            raise RequestError("invalid_window", "Require start < end and at most 1827 days.")
        groups = request.get("group_by", [])
        if not isinstance(groups, list) or any(not isinstance(group, str) for group in groups):
            raise RequestError("unsupported_grouping", "group_by must be a list of dimension names.")
        monthly = "month" in groups
        axes = [group for group in groups if group != "month"]
        required = SHARE_AXES.get(mid)
        if (len(groups) != len(set(groups)) or len(axes) > 1
                or (required and axes != [required])
                or (not required and axes and axes[0] not in spec["groups"])):
            raise RequestError("unsupported_grouping", "Use a supported axis and optionally month.")
        filters = request.get("filters", {})
        allowed = {"channel", "category"} if spec["family"] in CATEGORY_FAMILIES else {"channel"}
        if not isinstance(filters, dict) or filters.keys() - allowed:
            raise RequestError("unsupported_filter", "Filter is outside this metric's supported scope.")
        if "channel" in filters and (not isinstance(filters["channel"], str)
                                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", filters["channel"])):
            raise RequestError("invalid_filter", "Invalid channel code.")
        if "category" in filters and (type(filters["category"]) is not int
                                     or not 1 <= filters["category"] <= 2147483647):
            raise RequestError("invalid_filter", "Category must be a positive integer ID.")
        cutoff = request.get("observation_cutoff")
        if spec["family"] == "cohort":
            if cutoff != self.cutoff.date().isoformat() or end > self.cutoff.date():
                raise RequestError("unsupported_cutoff", "Use the published cohort cutoff and end <= cutoff.")
        elif "observation_cutoff" in request:
            raise RequestError("unsupported_cutoff", "This metric does not use a cohort cutoff.")
        return metric, start, end, monthly, axes[0] if axes else None, filters

    def query(self, request):
        mid = request.get("metric_id") if isinstance(request, dict) else None
        response = {"metric_id": mid if isinstance(mid, str) and mid in self.metrics else None,
                    "trace_id": uuid4().hex}
        try:
            metric, start, end, monthly, axis, filters = self.validate(request)
            template = self.templates[mid]
            sources = sorted(set(re.findall(r"(?:dws|dim)\.[a-z_]+", template)))
            response.update(definition=metric["definition"], definition_version=(
                self.catalog["approved_base"]["version"] if metric["definition_status"] == "approved_v1_unchanged"
                else self.catalog["catalog_version"]),
                event_time=metric["event_time"], time_semantics=metric["time_attribution"],
                source_grain=metric["grain"], group_by=request.get("group_by", []),
                start_date=start.isoformat(), end_date=end.isoformat(),
                observation_cutoff=request.get("observation_cutoff"), sources=sources)
            LOG.info("metric_query trace=%s metric=%s start=%s end=%s sources=%s",
                     response["trace_id"], mid, start, end, ",".join(sources))
            rows = self.execute(template, start, end, monthly, axis, filters)
            return dict(response, status="ok", results=rows, error=None)
        except RequestError as error:
            code, message = error.code, str(error)
        except psycopg.errors.QueryCanceled:
            code, message = "query_timeout", "The query exceeded its time budget."
        except psycopg.OperationalError:
            code, message = "database_unavailable", "Could not connect to the query database."
        except psycopg.Error:
            code, message = "query_failed", "The database could not execute this metric."
        LOG.info("metric_query_rejected trace=%s code=%s", response["trace_id"], code)
        return dict(response, status="error", results=[], error={"code": code, "message": message})

    def execute(self, template, start, end, monthly, axis, filters):
        if not self.dsn:
            raise RequestError("database_unavailable", "Metric database connection is not configured.")
        settings = conninfo_to_dict(self.dsn)
        if settings.get("user") != ROLE or settings.get("dbname") != "sqlshed":
            raise RequestError("unsafe_database_role", "Use the dedicated login and sqlshed database.")
        rows = []
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as pg:
            pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            pg.execute("SET LOCAL search_path = pg_catalog")
            pg.execute("SET LOCAL statement_timeout = '5s'")
            identity = pg.execute("SELECT session_user AS login, current_user AS role").fetchone()
            if identity != {"login": ROLE, "role": ROLE}:
                raise RequestError("unsafe_database_role", "Use the dedicated metric service login.")
            query = sql.SQL("SELECT * FROM ({}) AS metric ORDER BY bucket NULLS LAST LIMIT %(limit)s")
            query = query.format(metric_query(pg, template, axis))
            deadline = monotonic() + TIMEOUT_MS / 1000
            for lower, upper in windows(start, end, monthly):
                remaining = int((deadline - monotonic()) * 1000)
                if remaining <= 0:
                    raise RequestError("query_timeout", "The query exceeded its time budget.")
                pg.execute("SELECT set_config('statement_timeout', %s, true)", (str(remaining),))
                params = dict(start=lower, end=upper, as_of=self.cutoff,
                              channel=filters.get("channel"), category=filters.get("category"),
                              limit=ROW_LIMIT + 1 - len(rows))
                for row in pg.execute(query, params):
                    result = {key: json_value(value) for key, value in row.items()}
                    if monthly:
                        result.update(month=lower.strftime("%Y-%m"), start_date=lower.isoformat(),
                                      end_date=upper.isoformat())
                    rows.append(result)
                    if len(rows) > ROW_LIMIT:
                        raise RequestError("result_limit", "Narrow the window or grouping to at most 1000 rows.")
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON request object")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = dotenv_values(ROOT / ".env.metric-query")
    try:
        request = json.loads(args.request)
    except ValueError:
        request = None
    response = MetricQueryService(config.get("METRIC_QUERY_DSN")).query(request)
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
