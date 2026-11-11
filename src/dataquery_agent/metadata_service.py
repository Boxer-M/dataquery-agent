"""Read only PostgreSQL catalog metadata for the versioned SQL Shed inventory."""
from dataclasses import dataclass
from hashlib import sha256
import json

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from .metric_query_service import ROLE as METRIC_ROLE
from .metrics import ROOT, read_queries
from .warehouse import SOURCE_KEYS

MANIFESTS = ("warehouse/models.json", "warehouse/dws_models.json", "warehouse/ads_models.json")
CATALOG_SQL = "sql/warehouse/metadata/catalog.sql"
METADATA_ROLE = "sqlshed_reader"


@dataclass
class MetadataSnapshot:
    relations: dict
    schema_version: str
    source: str = "PostgreSQL pg_catalog"

    def get(self, name):
        return self.relations.get(name)


def manifest_models():
    models = {}
    for path in MANIFESTS:
        manifest = json.loads((ROOT / path).read_text())
        for model in manifest["models"]:
            name = model["name"]
            if name in models:
                raise ValueError(f"Duplicate versioned relation: {name}")
            models[name] = dict(model, manifest=path, model_version=manifest["model_version"])
    return models


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MetadataService:
    def __init__(self, config):
        if config.get("SQLSHED_USER") != METADATA_ROLE or config.get("SQLSHED_DATABASE") != "sqlshed":
            raise ValueError("Metadata requires the designated SQL Shed read-only login.")
        self.config = config

    @classmethod
    def local(cls):
        return cls(dotenv_values(ROOT / ".env.sqlshed-reader"))

    def snapshot(self):
        names = sorted(set(manifest_models()) | {f"ods.{name}" for name in SOURCE_KEYS})
        queries = read_queries(ROOT / CATALOG_SQL)
        config = self.config
        with psycopg.connect(host=config["SQLSHED_HOST"], port=config["SQLSHED_PORT"],
                             dbname=config["SQLSHED_DATABASE"], user=config["SQLSHED_USER"],
                             password=config["SQLSHED_PASSWORD"], connect_timeout=5,
                             row_factory=dict_row) as pg:
            pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '5s'")
            pg.execute("SET LOCAL search_path = pg_catalog")
            identity = pg.execute(
                "SELECT session_user AS login, current_user AS role, current_database() AS database"
            ).fetchone()
            if identity != {"login": METADATA_ROLE, "role": METADATA_ROLE, "database": "sqlshed"}:
                raise ValueError("Unexpected metadata connection identity.")
            params = {"names": names, "metric_role": METRIC_ROLE}
            relations = {row["name"]: dict(row, columns={}, constraints=[])
                         for row in pg.execute(queries["relations"], params)}
            for row in pg.execute(queries["columns"], params):
                relations[row.pop("name")]["columns"][row.pop("column")] = row
            for row in pg.execute(queries["constraints"], params):
                relations[row.pop("name")]["constraints"].append(row)
        structural = {
            name: {"kind": relation["kind"], "rls": relation["rls"], "constraints": relation["constraints"],
                   "columns": {column: {"type": spec["type"], "nullable": spec["nullable"]}
                               for column, spec in relation["columns"].items()}}
            for name, relation in relations.items()
        }
        return MetadataSnapshot(relations, fingerprint(structural))
