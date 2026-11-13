"""Immutable document versions and vectors, with a transactional active pointer."""
from contextlib import contextmanager
from dataclasses import asdict

import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from ..conversation_store import owner_hash
from ..metadata_service import fingerprint
from ..metrics import ROOT
from .documents import AUTHORITIES, PARSER_VERSION, KnowledgeError, validate_document

ADMIN = "sqlshed_knowledge_admin"
READER = "sqlshed_knowledge_reader"
DOCS = "agent_meta.knowledge_documents"
VERSIONS = "agent_meta.knowledge_versions"
CHUNKS = "agent_meta.knowledge_chunks"


class KnowledgeStore:
    def __init__(self, dsn, *, admin=False):
        self.dsn = dsn
        self.role = ADMIN if admin else READER
        config = conninfo_to_dict(dsn) if dsn else {}
        if (config.get("user"), config.get("dbname")) != (self.role, "sqlshed"):
            raise KnowledgeError("unsafe_knowledge_role")

    @classmethod
    def local(cls, *, admin=False):
        file = ".env.knowledge-admin" if admin else ".env.knowledge"
        return cls(dotenv_values(ROOT / file).get("KNOWLEDGE_DSN"), admin=admin)

    @contextmanager
    def transaction(self, owner=None, *, synthetic=False):
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as pg:
            if self.role == READER:
                pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '5s'")
            pg.execute("SET LOCAL lock_timeout = '2s'")
            pg.execute("SET LOCAL search_path = pg_catalog")
            identity = pg.execute("SELECT session_user, current_user, current_database()").fetchone()
            if tuple(identity.values()) != (self.role, self.role, "sqlshed"):
                raise KnowledgeError("unsafe_knowledge_role")
            pg.execute("SELECT set_config('agent.owner_scope',%s,true), set_config('agent.synthetic',%s,true)",
                       (owner_hash(owner) if owner else "", "on" if synthetic else "off"))
            yield pg

    @staticmethod
    def current(pg, document_id):
        return pg.execute(f"SELECT * FROM {DOCS} WHERE document_id=%s", (document_id,)).fetchone()

    @staticmethod
    def verify_version(pg, document_id, doc):
        stored = pg.execute(f"""SELECT title,content_hash,topic,effective_date::text,expires_on::text
            FROM {VERSIONS} WHERE document_id=%s AND document_version=%s""", (document_id, doc.version)).fetchone()
        if stored and tuple(stored.values()) != (doc.title, doc.content_hash, doc.topic, doc.effective, doc.expires):
            raise KnowledgeError("document_version_conflict")
        return stored

    def index(self, doc, embedding, *, owner=None, external_approved=False):
        validate_document(doc)
        if doc.source_type in {"catalog", "technical"} and owner:
            raise KnowledgeError("repository_sources_are_public")
        if doc.source_type in {"business_rule", "synthetic_fixture"}:
            if not owner:
                raise KnowledgeError("owner_scope_required")
            if not external_approved:
                raise KnowledgeError("external_embedding_confirmation_required")
        scope = owner_hash(owner) if owner else ""
        document_id = fingerprint([scope, doc.source_type, doc.source_uri])
        index = fingerprint([PARSER_VERSION, embedding.model, embedding.version])
        target = (doc.version, index)
        with self.transaction() as pg:
            previous = self.current(pg, document_id)
            known_version = self.verify_version(pg, document_id, doc)
            known_index = pg.execute(f"""SELECT 1 FROM {CHUNKS}
                WHERE document_id=%s AND document_version=%s AND index_version=%s LIMIT 1""",
                                     (document_id, *target)).fetchone()
        before = (previous["active_version"], previous["active_index"]) if previous else (None, None)
        if previous and previous["deleted_at"]:
            raise KnowledgeError("document_deleted")
        if before == target:
            return {"document_id": document_id, "version": doc.version, "indexed": False}
        if known_version and before[0] not in (None, doc.version):
            raise KnowledgeError("cannot_reactivate_superseded_version")
        values = None if known_index else embedding.embed(
            ["\n".join([doc.title, *chunk.heading_path, chunk.text]) for chunk in doc.passages])
        if values is not None and len(values) != len(doc.passages):
            raise KnowledgeError("invalid_embedding_response")
        with self.transaction() as pg:
            pg.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("knowledge:" + document_id,))
            current = self.current(pg, document_id)
            self.verify_version(pg, document_id, doc)
            if current:
                if current["deleted_at"]:
                    raise KnowledgeError("document_deleted")
                pointer = (current["active_version"], current["active_index"])
                if pointer == target:
                    return {"document_id": document_id, "version": doc.version, "indexed": False}
                if pointer != before:
                    raise KnowledgeError("document_updated_concurrently")
            else:
                pg.execute(f"""INSERT INTO {DOCS} (document_id,owner_scope,source_type,source_uri)
                    VALUES (%s,%s,%s,%s)""", (document_id, scope, doc.source_type, doc.source_uri))
            pg.execute(f"""INSERT INTO {VERSIONS}
                (document_id,document_version,title,content_hash,topic,authority_level,effective_date,expires_on)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                       (document_id, doc.version, doc.title, doc.content_hash, doc.topic,
                        AUTHORITIES[doc.source_type], doc.effective, doc.expires))
            for i, (chunk, item) in enumerate(zip(doc.passages, values or [])):
                content_hash = fingerprint(asdict(chunk))
                chunk_id = fingerprint([document_id, doc.version, index, i, content_hash])
                pg.execute(f"""INSERT INTO {CHUNKS}
                    (chunk_id,document_id,document_version,index_version,chunk_index,chunk_text,heading_path,
                     source_location,content_hash,embedding_model,embedding_version,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::rag_vector.vector)""",
                           (chunk_id, document_id, doc.version, index, i, chunk.text, list(chunk.heading_path),
                            chunk.location, content_hash, embedding.model, embedding.version, item))
            pg.execute(f"UPDATE {DOCS} SET active_version=%s, active_index=%s WHERE document_id=%s",
                       (*target, document_id))
        return {"document_id": document_id, "version": doc.version, "indexed": True, "chunks": len(doc.passages)}

    def delete(self, document_id, *, owner=None):
        scope = owner_hash(owner) if owner else ""
        with self.transaction() as pg:
            pg.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("knowledge:" + document_id,))
            result = pg.execute(f"""UPDATE {DOCS} SET deleted_at=now(), active_version=NULL, active_index=NULL
                WHERE document_id=%s AND owner_scope=%s AND deleted_at IS NULL""", (document_id, scope))
            return result.rowcount == 1

    def list_versions(self, *, owner=None):
        scope = owner_hash(owner) if owner else ""
        with self.transaction() as pg:
            return pg.execute(f"""SELECT d.document_id,d.source_type,d.source_uri,d.active_version,d.deleted_at,v.*
                FROM {DOCS} d JOIN {VERSIONS} v USING(document_id)
                WHERE d.owner_scope=%s ORDER BY d.source_uri,v.imported_at""", (scope,)).fetchall()

    def chunks(self, document_id, *, owner=None):
        scope = owner_hash(owner) if owner else ""
        with self.transaction() as pg:
            return pg.execute(f"""SELECT c.chunk_id,c.document_version,c.chunk_index,c.chunk_text,c.heading_path,
                c.source_location,c.embedding_version,c.last_indexed_at
                FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
                WHERE c.document_id=%s AND d.owner_scope=%s ORDER BY c.last_indexed_at,c.chunk_index""",
                              (document_id, scope)).fetchall()

    def catalog_chunks(self, documents, *, today, owner=None):
        """Read current catalog evidence through the existing RLS policies."""
        with self.transaction(owner) as pg:
            return pg.execute(f"""
                SELECT d.document_id,d.source_type,d.source_uri,false AS private,
                       v.document_version,v.title,v.topic,v.authority_level,v.effective_date::text AS effective_date,
                       v.content_hash AS document_hash,c.chunk_id,c.chunk_text,c.heading_path,c.source_location,
                       c.content_hash,c.embedding_version,NULL AS retrieval_score,'active' AS evidence_date_status
                FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
                JOIN {VERSIONS} v USING(document_id,document_version)
                WHERE d.source_type='catalog' AND d.owner_scope=''
                  AND d.deleted_at IS NULL AND d.active_version=c.document_version AND d.active_index=c.index_version
                  AND (d.source_uri,v.topic,v.document_version,v.content_hash) IN (
                      SELECT * FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[])
                  )
                  AND (v.effective_date IS NULL OR v.effective_date<=%s)
                  AND (v.expires_on IS NULL OR v.expires_on>%s)
                ORDER BY c.chunk_index,c.chunk_id
            """, ([doc.source_uri for doc in documents], [doc.topic for doc in documents],
                  [doc.version for doc in documents], [doc.content_hash for doc in documents], today, today)).fetchall()
