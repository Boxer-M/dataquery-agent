"""Versioned asset storage. The runtime login can only read registry versions."""
from contextlib import contextmanager

import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .metadata_service import fingerprint
from .metrics import ROOT

ADMIN = "sqlshed_asset_admin"
READER = "sqlshed_asset_reader"
VERSIONS = "agent_meta.query_asset_versions"
EVENTS = "agent_meta.query_asset_events"
TRANSITIONS = {
    "CANDIDATE": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"PUBLISHED", "REJECTED"},
    "PUBLISHED": {"STALE", "RETIRED"},
    "STALE": {"RETIRED"},
}


class AssetError(ValueError):
    pass


class QueryAssetStore:
    def __init__(self, dsn, *, admin=False):
        self.role = ADMIN if admin else READER
        self.dsn = dsn
        if not dsn or (conninfo_to_dict(dsn).get("user"), conninfo_to_dict(dsn).get("dbname")) != (
                self.role, "sqlshed"):
            raise AssetError("unsafe_asset_role")

    @classmethod
    def local(cls, *, admin=False):
        path = ".env.query-asset-admin" if admin else ".env.query-assets"
        return cls(dotenv_values(ROOT / path).get("QUERY_ASSET_DSN"), admin=admin)

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as pg:
            if self.role == READER:
                pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '5s'")
            pg.execute("SET LOCAL lock_timeout = '2s'")
            pg.execute("SET LOCAL search_path = pg_catalog")
            if tuple(pg.execute("SELECT session_user, current_user, current_database()").fetchone().values()) != (
                    self.role, self.role, "sqlshed"):
                raise AssetError("unsafe_asset_role")
            yield pg

    @staticmethod
    def lock(pg, operation, *, shared=False):
        function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        pg.execute(f"SELECT {function}(hashtextextended(%s, 0))", ("query-asset:" + operation,))

    @staticmethod
    def event(pg, row, status, evidence=None):
        pg.execute(f"""INSERT INTO {EVENTS}
            (asset_id, implementation_version, lifecycle_status, actor, evidence)
            VALUES (%s,%s,%s,session_user,%s)""",
                   (row["asset_id"], row["implementation_version"], status, Jsonb(evidence)))

    def create(self, definition):
        operation = definition["operation"]
        asset_id = definition["asset_id"]
        identity = fingerprint([definition["semantic_hash"], definition["canonical_sql"],
                                definition["parameter_schema"], definition["metadata_fingerprint"]])
        with self.transaction() as pg:
            self.lock(pg, operation)
            old = pg.execute(f"SELECT * FROM {VERSIONS} WHERE asset_id=%s ORDER BY implementation_version DESC",
                             (asset_id,)).fetchall()
            for item in old:
                if item["identity_hash"] == identity:
                    return item
            version = old[0]["implementation_version"] if old else 0
            row = pg.execute(f"""INSERT INTO {VERSIONS} (asset_id, implementation_version, operation,
                identity_hash, definition, supersedes_version) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                             (asset_id, version + 1, operation, identity, Jsonb(definition), version or None)).fetchone()
            self.event(pg, row, "CANDIDATE", definition["provenance"])
            return row

    @staticmethod
    def get(pg, asset_id, version):
        row = pg.execute(f"SELECT * FROM {VERSIONS} WHERE asset_id=%s AND implementation_version=%s",
                         (asset_id, version)).fetchone()
        if row is None:
            raise AssetError("asset_not_found")
        return row

    def read(self, asset_id, version):
        with self.transaction() as pg:
            return self.get(pg, asset_id, version)

    def transition(self, asset_id, version, status, *, evidence=None, check=None):
        with self.transaction() as pg:
            row = self.get(pg, asset_id, version)
            self.lock(pg, row["operation"])
            row = self.get(pg, asset_id, version)
            if status not in TRANSITIONS.get(row["lifecycle_status"], ()):
                raise AssetError("invalid_asset_transition")
            if status in {"VALIDATED", "PUBLISHED"}:
                if check is None:
                    raise AssetError("validation_required")
                check(row)
            if status == "PUBLISHED":
                newer = pg.execute(f"""SELECT 1 FROM {VERSIONS} WHERE asset_id=%s
                    AND implementation_version>%s AND published_at IS NOT NULL""", (asset_id, version)).fetchone()
                if newer:
                    raise AssetError("asset_version_superseded")
                old = pg.execute(f"""UPDATE {VERSIONS} SET lifecycle_status='RETIRED', updated_at=now()
                    WHERE operation=%s AND lifecycle_status IN ('PUBLISHED','STALE') RETURNING *""",
                                 (row["operation"],)).fetchall()
                for item in old:
                    self.event(pg, item, "RETIRED", {"replaced_by": version, "asset_id": asset_id})
            updated = pg.execute(f"""UPDATE {VERSIONS} SET lifecycle_status=%s, updated_at=now(),
                validation_evidence=CASE WHEN %s='VALIDATED' THEN %s ELSE validation_evidence END,
                published_at=CASE WHEN %s='PUBLISHED' THEN now() ELSE published_at END
                WHERE asset_id=%s AND implementation_version=%s RETURNING *""",
                                 (status, status, Jsonb(evidence), status, asset_id, version)).fetchone()
            self.event(pg, updated, status, evidence)
            return updated

    @contextmanager
    def active(self, operation):
        with self.transaction() as pg:
            self.lock(pg, operation, shared=True)
            yield pg.execute(f"""SELECT * FROM {VERSIONS}
                WHERE operation=%s AND lifecycle_status IN ('PUBLISHED','STALE')
                ORDER BY (lifecycle_status='PUBLISHED') DESC, published_at DESC LIMIT 1""",
                             (operation,)).fetchone()
