"""Private SQLite BLOB artifacts: workbook and metadata commit atomically."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from .conversation_store import identifier, owner_hash
from .metrics import ROOT

MAX_BYTES = 1024 * 1024
MAX_STORE_BYTES = 64 * 1024 * 1024
SCHEMA = """CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY, owner TEXT NOT NULL, query_id TEXT NOT NULL,
    filename TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL, content BLOB NOT NULL
);"""


class ArtifactError(ValueError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status


class ArtifactStore:
    def __init__(self, directory=ROOT / "data/staging/xlsx-artifacts"):
        self.directory = Path(directory)

    @contextmanager
    def connect(self, *, write=False):
        # Match DocumentStaging's loopback-local private storage boundary.
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if self.directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ArtifactError(503, "artifact_storage_permissions")
        path = self.directory / "artifacts.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ArtifactError(503, "artifact_storage_permissions")
        finally:
            os.close(fd)
        with closing(sqlite3.connect(path, timeout=0.2, autocommit=True)) as db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                if write and db.execute("SELECT coalesce(sum(byte_size),0) FROM artifacts").fetchone()[0] > MAX_STORE_BYTES:
                    raise ArtifactError(507, "artifact_storage_quota")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _insert(db, record, content):
        db.execute("""INSERT INTO artifacts
            (artifact_id,owner,query_id,filename,created_at,sha256,byte_size,content)
            VALUES (?,?,?,?,?,?,?,?)""", (*record.values(), content))

    def create(self, owner, query_id, content):
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
            raise ArtifactError(413, "artifact_size_limit")
        aid = str(uuid4())
        record = dict(artifact_id=aid, owner=owner_hash(owner), query_id=str(identifier(query_id)),
                      filename=f"metrics-{aid}.xlsx", created_at=datetime.now(timezone.utc).isoformat(),
                      sha256=sha256(content).hexdigest(), byte_size=len(content))
        with self.connect(write=True) as db:
            self._insert(db, record, content)
        return {key: value for key, value in record.items() if key != "owner"}

    def get(self, owner, artifact_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM artifacts WHERE artifact_id=? AND owner=?",
                             (str(identifier(artifact_id)), owner_hash(owner))).fetchone()
        if (row is None or row["filename"] != f'metrics-{row["artifact_id"]}.xlsx'
                or not isinstance(row["content"], bytes) or not row["content"] or len(row["content"]) != row["byte_size"]
                or sha256(row["content"]).hexdigest() != row["sha256"]):
            raise ArtifactError(404, "artifact_not_found")
        return dict(row)
