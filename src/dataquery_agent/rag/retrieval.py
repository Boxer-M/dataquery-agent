"""Retrieve visible active chunks using pgvector cosine distance and exact terms."""
from datetime import date
from time import monotonic
from uuid import uuid4
import re
import unicodedata

from .documents import KnowledgeError, ensure_safe
from .store import CHUNKS, DOCS, VERSIONS

STRATEGY = "pgvector-cosine-exact-v1"


class Retriever:
    def __init__(self, store, embedding):
        self.store = store
        self.embedding = embedding

    def search(self, question, *, today, owner=None, synthetic=False, topics=None, rules=False, top_k=6):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500 or type(top_k) is not int or not 1 <= top_k <= 10:
            raise KnowledgeError("invalid_retrieval_request")
        if not isinstance(today, date) or (topics is not None and
                                            (len(topics) > 10 or any(not isinstance(x, str) for x in topics))):
            raise KnowledgeError("invalid_retrieval_request")
        query = " ".join(unicodedata.normalize("NFKC", question).split())
        ensure_safe(query)
        started = monotonic()
        values = self.embedding.embed([query], query=True)[0]
        exact_terms = re.findall(r"[a-z][a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", query.lower())[:10]
        with self.store.transaction(owner, synthetic=synthetic) as pg:
            rows = pg.execute(f"""
                WITH scored AS (
                    SELECT d.document_id,d.source_type,d.source_uri,d.owner_scope <> '' AS private,
                           v.document_version,v.title,v.topic,v.authority_level,v.effective_date,v.expires_on,
                           v.content_hash AS document_hash,c.chunk_id,c.chunk_text,c.heading_path,c.source_location,
                           c.content_hash,c.embedding_model,c.embedding_version,
                           1-(c.embedding OPERATOR(rag_vector.<=>) %(embedding)s::rag_vector.vector) AS retrieval_score,
                           EXISTS (SELECT 1 FROM unnest(%(terms)s::text[]) term
                                   WHERE strpos(lower(v.title || ' ' || c.chunk_text), term) > 0) AS exact_match,
                           CASE WHEN v.effective_date > %(today)s THEN 'future'
                                WHEN v.expires_on <= %(today)s THEN 'expired' ELSE 'active' END AS evidence_date_status
                    FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
                    JOIN {VERSIONS} v USING(document_id,document_version)
                    WHERE c.embedding_version=%(version)s AND c.embedding_model=%(model)s
                      AND (%(topics)s::text[] IS NULL OR (d.source_type='catalog' AND v.topic=ANY(%(topics)s)))
                      AND (NOT %(rules)s OR d.source_type IN ('business_rule','synthetic_fixture'))
                )
                SELECT * FROM scored
                ORDER BY (evidence_date_status='active') DESC,
                         (retrieval_score + CASE WHEN exact_match THEN 0.08 ELSE 0 END) DESC, chunk_id
                LIMIT %(limit)s
            """, {"embedding": values, "terms": exact_terms, "today": today, "version": self.embedding.version,
                  "model": self.embedding.model, "topics": topics, "rules": rules, "limit": min(3 * top_k, 30)}).fetchall()
            if not rows:
                compatible = pg.execute(f"""
                    SELECT EXISTS (SELECT 1 FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
                        JOIN {VERSIONS} v USING(document_id,document_version)
                        WHERE (%s::text[] IS NULL OR (d.source_type='catalog' AND v.topic=ANY(%s)))
                          AND (NOT %s OR d.source_type IN ('business_rule','synthetic_fixture')))
                """, (topics, topics, rules)).fetchone()
                incompatible = next(iter(compatible.values()))
            else:
                incompatible = False
        for row in rows:
            if row["evidence_date_status"] != "active":
                row["chunk_text"] = ""
            for key in ("effective_date", "expires_on"):
                if row[key]:
                    row[key] = row[key].isoformat()
        rows = [row for row in rows if topics is not None or row["exact_match"] or row["retrieval_score"] >= 0.43]
        trace = {
            "trace_id": uuid4().hex, "strategy": STRATEGY, "embedding_model": self.embedding.model,
            "embedding_version": self.embedding.version, "index_incompatible": incompatible,
            "query": query, "scope": "rules" if rules else "catalog" if topics else "all", "elapsed_ms": round((monotonic() - started) * 1000),
            "candidates": [{key: row[key] for key in ("document_id", "document_version", "chunk_id", "retrieval_score",
                                                     "exact_match", "evidence_date_status", "source_location", "heading_path")}
                           for row in rows],
        }
        return rows[:top_k], trace
