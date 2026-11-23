"""Owner-scoped pgvector and independent PostgreSQL FTS candidates, fused by RRF."""
from datetime import date
from time import monotonic
from uuid import uuid4
import re
import unicodedata
import os
from dotenv import dotenv_values

from .documents import KnowledgeError, ensure_safe
from .store import CHUNKS, DOCS, VERSIONS
from ..metrics import ROOT
from .vector_candidates import PgVectorCandidates,MilvusCandidates

STRATEGY = "pgvector-postgres-fts-rrf-v1"


def keyword_terms(text):
    """Explicit CJK bigrams plus simple English/numeric lexemes, not a Chinese word segmenter."""
    terms = re.findall(r"[a-z0-9_]{2,}", text.lower())
    terms += [run[i:i + 2] for run in re.findall(r"[\u4e00-\u9fff]+", text) for i in range(len(run) - 1)]
    return list(dict.fromkeys(terms))[:100]


def rrf(vector_rows, keyword_rows, constant=60):
    scores, by_id = {}, {}
    for channel, rows in (("vector", vector_rows), ("keyword", keyword_rows)):
        seen = set()
        for rank, row in enumerate(rows, 1):
            key = row["chunk_id"]
            if key in seen:
                continue
            seen.add(key)
            by_id.setdefault(key, dict(row))[channel + "_rank"] = rank
            scores[key] = scores.get(key, 0) + 1 / (constant + rank)
    return [dict(by_id[key], rrf_score=scores[key]) for key in sorted(
        by_id, key=lambda key: (by_id[key]["evidence_date_status"] != "active", -scores[key], key))]


class Retriever:
    def __init__(self, store, embedding, *, mode="hybrid", backend=None, milvus=None):
        if mode not in {"vector", "hybrid"}:
            raise KnowledgeError("invalid_retrieval_mode")
        self.store = store
        self.embedding = embedding
        self.mode = mode
        backend=backend or os.getenv('RAG_VECTOR_BACKEND') or dotenv_values(ROOT/'.env.milvus').get('RAG_VECTOR_BACKEND','pgvector')
        if backend not in {'pgvector','milvus'}:
            raise KnowledgeError('invalid_vector_backend')
        self.candidates=PgVectorCandidates() if backend=='pgvector' else MilvusCandidates(milvus)

    def search(self, question, *, today, owner=None, synthetic=False, topics=None, rules=False, top_k=6, reviewed_pdf=False, include_reviewed_pdf=False):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500 or type(top_k) is not int or not 1 <= top_k <= 10:
            raise KnowledgeError("invalid_retrieval_request")
        if not isinstance(today, date) or (topics is not None and
                                            (len(topics) > 10 or any(not isinstance(x, str) for x in topics))):
            raise KnowledgeError("invalid_retrieval_request")
        if reviewed_pdf and (not owner or synthetic or topics is not None or rules):
            raise KnowledgeError("invalid_pdf_retrieval_scope")
        include_reviewed_pdf = bool(include_reviewed_pdf and owner and not synthetic and topics is None)
        allow_pdf = reviewed_pdf or include_reviewed_pdf
        pdf_provenance = ",v.pdf_review,'published' AS publication_state" if allow_pdf else ""
        pdf_gate = """AND (d.source_type <> 'reviewed_pdf' OR (
            d.owner_scope = current_setting('agent.owner_scope', true)
            AND d.deleted_at IS NULL AND d.active_version = c.document_version AND d.active_index = c.index_version
            AND v.pdf_review->>'review_state' = 'reviewed'
            AND v.pdf_review->>'file_sha256' = v.content_hash
            AND v.pdf_review->>'owner_scope' = d.owner_scope))""" if allow_pdf else ""
        query = " ".join(unicodedata.normalize("NFKC", question).split())
        ensure_safe(query)
        started = monotonic()
        values = self.embedding.embed([query], query=True)[0]
        exact_terms = re.findall(r"[a-z][a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", query.lower())[:10]
        terms = keyword_terms(query)
        params = {"embedding": values, "terms": exact_terms, "today": today, "version": self.embedding.version,
                  "model": self.embedding.model, "pdf_only": reviewed_pdf, "allow_pdf": allow_pdf, "topics": topics,
                  "rules": rules, "limit": min(3 * top_k, 30), "lexical": " | ".join(terms)}
        with self.store.transaction(owner, synthetic=synthetic) as pg:
            scored = f"""
                WITH scored AS (
                    SELECT d.document_id,d.source_type,d.source_uri,d.owner_scope <> '' AS private,
                           v.document_version,v.title,v.topic,v.authority_level,v.effective_date,v.expires_on,
                           v.content_hash AS document_hash,c.chunk_id,c.chunk_text,c.heading_path,c.source_location{pdf_provenance},
                           c.content_hash,c.embedding_model,c.embedding_version,c.index_version,
                           1-(c.embedding OPERATOR(rag_vector.<=>) %(embedding)s::rag_vector.vector) AS retrieval_score,
                           EXISTS (SELECT 1 FROM unnest(%(terms)s::text[]) term
                                   WHERE strpos(lower(v.title || ' ' || c.chunk_text), term) > 0) AS exact_match,
                           CASE WHEN v.effective_date > %(today)s THEN 'future'
                                WHEN v.expires_on <= %(today)s THEN 'expired' ELSE 'active' END AS evidence_date_status
                    FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
                    JOIN {VERSIONS} v USING(document_id,document_version)
                    WHERE c.embedding_version=%(version)s AND c.embedding_model=%(model)s
                      AND (NOT %(pdf_only)s OR d.source_type='reviewed_pdf')
                      AND (%(allow_pdf)s OR d.source_type<>'reviewed_pdf')
                      {pdf_gate}
                      AND (%(topics)s::text[] IS NULL OR (d.source_type='catalog' AND v.topic=ANY(%(topics)s)))
                      AND (NOT %(rules)s OR d.source_type IN ('business_rule','synthetic_fixture','reviewed_pdf'))
                )
            """
            vector_rows,backend_trace=self.candidates.search(pg,scored,params)
            vector_pool_ids = [row["chunk_id"] for row in vector_rows]
            has_compatible_index = backend_trace['compatible_index']
            vector_rows = [row for row in vector_rows if topics is not None or row["retrieval_score"] >= 0.43]
            keyword_rows = []
            if self.mode == "hybrid" and terms:
                # Independent query against ALL visible chunks, never the vector LIMIT result.
                # The SQL tokenizer mirrors keyword_terms; no extension or unreviewed dictionary required.
                keyword_rows = pg.execute(scored + """
                    , lexical AS (
                      SELECT scored.*,to_tsvector('pg_catalog.simple',title || ' ' || chunk_text || ' ' ||
                        coalesce((SELECT string_agg(substr(title || ' ' || chunk_text,i,2),' ')
                          FROM generate_series(1,length(title || ' ' || chunk_text)-1) i
                          WHERE substr(title || ' ' || chunk_text,i,2) ~ '^[一-鿿]{2}$'),'')) AS lexemes
                      FROM scored WHERE evidence_date_status='active'
                    )
                    SELECT lexical.*,ts_rank_cd(lexemes,to_tsquery('pg_catalog.simple',%(lexical)s)) AS keyword_score
                    FROM lexical WHERE lexemes @@ to_tsquery('pg_catalog.simple',%(lexical)s)
                    ORDER BY keyword_score DESC,chunk_id LIMIT %(limit)s
                """, params).fetchall()
            if not vector_rows and not keyword_rows and not has_compatible_index:
                compatible = pg.execute(f"""
                    SELECT EXISTS (SELECT 1 FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
                        JOIN {VERSIONS} v USING(document_id,document_version)
                        WHERE (NOT %s OR d.source_type='reviewed_pdf')
                          AND (%s OR d.source_type<>'reviewed_pdf')
                          {pdf_gate}
                          AND (%s::text[] IS NULL OR (d.source_type='catalog' AND v.topic=ANY(%s)))
                          AND (NOT %s OR d.source_type IN ('business_rule','synthetic_fixture','reviewed_pdf')))
                """, (reviewed_pdf, allow_pdf, topics, topics, rules)).fetchone()
                incompatible = next(iter(compatible.values()))
            else:
                incompatible = False
        # A fresh snapshot after the external call closes the stale-version/withdrawal
        # window before evidence is handed to the model. Final citation validation remains.
        ids=list({row['chunk_id'] for row in vector_rows+keyword_rows})
        if ids:
            with self.store.transaction(owner,synthetic=synthetic) as pg:
                current=pg.execute(scored+'SELECT * FROM scored WHERE chunk_id=ANY(%(candidate_ids)s)',
                                   params|{'candidate_ids':ids}).fetchall()
            current={row['chunk_id']:row for row in current}
            backend_trace['final_pg_rejected_ids']=sorted(set(ids)-set(current))
            vector_rows=[row|current[row['chunk_id']] for row in vector_rows if row['chunk_id'] in current]
            keyword_rows=[row|current[row['chunk_id']] for row in keyword_rows if row['chunk_id'] in current]
        rows = rrf(vector_rows, keyword_rows) if self.mode == "hybrid" else vector_rows
        for row in rows:
            if row["evidence_date_status"] != "active":
                row["chunk_text"] = ""
            for key in ("effective_date", "expires_on"):
                if row[key]:
                    row[key] = row[key].isoformat()
        trace = {
            "trace_id": uuid4().hex, "strategy": (STRATEGY if backend_trace['actual']=='pgvector' else 'milvus-postgres-fts-rrf-v1') if self.mode == "hybrid" else backend_trace['actual']+'-cosine-v2', "query_embedding_called": True, "embedding_model": self.embedding.model,
            "mode": self.mode, "fusion": "rrf" if self.mode == "hybrid" else None, "rrf_constant": 60,
            "vector_backend": backend_trace,
            "keyword_algorithm": "postgresql-simple-fts-cjk-bigram-v1" if self.mode == "hybrid" else None,
            "keyword_terms": terms if self.mode == "hybrid" else [],
            "channels": {"vector_pool": vector_pool_ids, "vector": [r["chunk_id"] for r in vector_rows], "keyword": [r["chunk_id"] for r in keyword_rows]},
            "top_k": top_k, "returned_chunk_ids": [row["chunk_id"] for row in rows[:top_k]],
            "embedding_version": self.embedding.version, "index_incompatible": incompatible,
            "query": query, "includes_reviewed_pdf": allow_pdf, "scope": "reviewed_pdf" if reviewed_pdf else "rules" if rules else "catalog" if topics else "all", "elapsed_ms": round((monotonic() - started) * 1000),
            "candidates": [{key: row[key] for key in ("document_id", "document_version", "chunk_id", "retrieval_score",
                                                     "exact_match", "evidence_date_status", "source_location", "heading_path", "source_type")}
                           | {key: row[key] for key in ("rrf_score", "vector_rank", "keyword_rank") if key in row}
                           for row in rows],
        }
        return rows[:top_k], trace
