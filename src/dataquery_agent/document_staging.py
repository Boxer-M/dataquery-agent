"""Private local staging for PDF uploads and signed reviews."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4

from .conversation_store import owner_hash
from .document_ingestion import extract_pdf
from .metrics import ROOT
from .rag.embedding import GeminiEmbedding
from .rag.pdf_review import approve, build_document
from .rag.store import KnowledgeStore

MAX_STAGE_BYTES = 128 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    document_id TEXT NOT NULL, owner TEXT NOT NULL, version TEXT PRIMARY KEY NOT NULL,
    pdf BLOB NOT NULL, extraction TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS uploads_owner_document ON uploads(owner, document_id);
CREATE TABLE IF NOT EXISTS reviews (
    upload TEXT NOT NULL REFERENCES uploads(version), version TEXT NOT NULL, receipt TEXT NOT NULL,
    publication TEXT, UNIQUE(upload, version)
);
"""


class StageError(ValueError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status


@contextmanager
def local_pdf(raw):
    with TemporaryDirectory(prefix="dataquery-upload-") as directory:
        path = Path(directory) / "input.pdf"
        path.write_bytes(raw)
        yield path


class DocumentStaging:
    def __init__(self, directory=ROOT / "data/staging/document-api", *, publisher=None, embedding=None):
        self.directory = directory
        self.publisher = publisher
        self.embedding = embedding

    @contextmanager
    def connect(self, *, write=False):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if self.directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise StageError(503, "staging_permissions")
        path = self.directory / "uploads.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise StageError(503, "staging_permissions")
        finally:
            os.close(fd)
        with closing(sqlite3.connect(path, timeout=0.2, autocommit=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                if write:
                    size = db.execute("""SELECT
                        coalesce((SELECT sum(length(pdf)+length(CAST(extraction AS BLOB))) FROM uploads),0)
                        + coalesce((SELECT sum(length(CAST(receipt AS BLOB))) FROM reviews),0)
                        """).fetchone()[0]
                    if size > MAX_STAGE_BYTES:
                        raise StageError(507, "staging_quota")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def current(db, owner, document_id, *, include_pdf=False):
        fields = "*" if include_pdf else "document_id, version, extraction"
        upload = db.execute(f"""SELECT {fields} FROM uploads WHERE owner=? AND document_id=?
                              ORDER BY rowid DESC LIMIT 1""", (owner_hash(owner), document_id)).fetchone()
        if upload is None:
            raise StageError(404, "document_not_found")
        review = db.execute("""SELECT * FROM reviews WHERE upload=? ORDER BY rowid DESC LIMIT 1""",
                            (upload["version"],)).fetchone()
        return upload, review

    @staticmethod
    def preview(upload, review, page=None):
        extraction = json.loads(upload["extraction"])
        pages = extraction["pages"]
        receipt = json.loads(review["receipt"])["review"] if review else None
        metadata = {
            "document_id": upload["document_id"], "version": upload["version"],
            "file_sha256": extraction["file_sha256"], "extraction_version": extraction["extraction_version"],
            "ocr_bounds_space": extraction["ocr_bounds_space"],
            "state": "published" if review and review["publication"] else "reviewed" if review else "unreviewed",
            "review_version": review["version"] if review else None,
            "title": receipt["title"] if receipt else None,
            "review_basis": receipt["basis"] if receipt else None,
            "publication": json.loads(review["publication"]) if review and review["publication"] else None,
            "pages": [{key: row[key] for key in ("page", "method", "status", "error", "extractor_version")}
                      for row in pages],
        }
        if page is not None:
            if not 1 <= page <= len(pages):
                raise StageError(404, "page_not_found")
            reviewed = next((row for row in receipt["pages"] if row["page"] == page), None) if receipt else None
            metadata["page"] = {**pages[page - 1], "reviewed_text": reviewed["text"] if reviewed else None}
        return metadata

    def get(self, owner, document_id, page=None):
        with self.connect() as db:
            return self.preview(*self.current(db, owner, document_id), page=page)

    def import_pdf(self, owner, raw, document_id=None):
        with self.connect(write=True) as db:
            if document_id is not None:
                upload, review = self.current(db, owner, document_id, include_pdf=True)
                if upload["pdf"] == raw:
                    return self.preview(upload, review)
            else:
                document_id = str(uuid4())
            with local_pdf(raw) as path:
                extraction = extract_pdf(path, owner=owner)
            db.execute("INSERT INTO uploads VALUES (?, ?, ?, ?, ?)",
                       (document_id, owner_hash(owner), str(uuid4()), raw, json.dumps(extraction)))
            return self.preview(*self.current(db, owner, document_id))

    def review(self, owner, document_id, version, *, title, basis, corrections):
        with self.connect(write=True) as db:
            upload, current = self.current(db, owner, document_id, include_pdf=True)
            if version != upload["version"]:
                raise StageError(409, "stale_upload_version")
            with local_pdf(upload["pdf"]) as path:
                receipt = approve(json.loads(upload["extraction"]), path, owner=owner, slug=f"api-{document_id}",
                                  title=title, basis=basis, corrections=corrections, source_revision=version)
            review_version = build_document(receipt["review"]).version
            if not current or current["version"] != review_version:
                try:
                    db.execute("INSERT INTO reviews (upload, version, receipt) VALUES (?, ?, ?)",
                               (version, review_version, json.dumps(receipt)))
                except sqlite3.IntegrityError:
                    raise StageError(409, "superseded_review") from None
            return self.preview(*self.current(db, owner, document_id))

    def publish(self, owner, document_id, version, review_version):
        with self.connect(write=True) as db:
            upload, review = self.current(db, owner, document_id, include_pdf=True)
            if version != upload["version"]:
                raise StageError(409, "stale_upload_version")
            if not review or review_version != review["version"]:
                raise StageError(409, "current_review_required")
            receipt = json.loads(review["receipt"])
            if (receipt.get("review", {}).get("source_revision") != version
                    or receipt["review"].get("source_uri") != f"reviewed-pdf:api-{document_id}"
                    or build_document(receipt["review"]).version != review_version):
                raise StageError(409, "review_source_mismatch")
            publisher = self.publisher or KnowledgeStore.local(admin=True)
            embedding = self.embedding or GeminiEmbedding.local()
            with local_pdf(upload["pdf"]) as path:
                result = publisher.index(None, embedding, owner=owner, external_approved=True,
                                         pdf_receipt=receipt, pdf=path)
            publication = {key: result[key] for key in ("document_id", "version", "indexed")}
            db.execute("UPDATE reviews SET publication=? WHERE upload=? AND version=?",
                       (json.dumps(publication), version, review_version))
            return self.preview(*self.current(db, owner, document_id))
