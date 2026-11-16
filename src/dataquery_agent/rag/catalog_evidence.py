"""Resolve citations against the current formal catalog and its active index."""
from dataclasses import asdict
from time import monotonic
from uuid import uuid4

import psycopg

from ..metadata_service import fingerprint
from .answer import build_evidence, validate_claim
from .documents import KnowledgeError, catalog_document


def exact_definitions(store, service, resolution, *, today, owner=None, missing=None):
    started = monotonic()
    ids = resolution["metric_ids"]
    documents = [catalog_document(mid, service.metrics[mid], service.catalog["catalog_version"]) for mid in ids]
    trace = {"trace_id": uuid4().hex, "strategy": "catalog_exact", "query_embedding_called": False,
             "resolution_method": resolution["method"], "metric_ids": ids, "candidates": []}
    citations, lines, uncited = [], [], []
    try:
        rows = store.catalog_chunks(documents, today=today, owner=owner) if documents else []
    except (psycopg.Error, KnowledgeError, OSError):
        rows = []
        trace["reason"] = "catalog_index_unavailable"
    for doc in documents:
        passage = doc.passages[0]
        current = [row for row in rows if row["topic"] == doc.topic and row["source_uri"] == doc.source_uri
                   and row["document_version"] == doc.version and row["chunk_text"] == passage.text
                   and row["source_location"] == passage.location and row["heading_path"] == list(passage.heading_path)
                   and row["content_hash"] == fingerprint(asdict(passage))]
        evidence, _, _ = build_evidence(current, service.metrics)
        definition = service.metrics[doc.topic]["definition"]
        line = f"{doc.title}：{definition}"
        if evidence:
            item = evidence[0]
            citation = validate_claim({"chunk_id": item["chunk_id"], "quote": definition}, {item["chunk_id"]: item})
            citations.append(citation)
            line += f" [{len(citations)}] {doc.source_uri} · 版本 {doc.version}"
        else:
            uncited.append(doc.topic)
            line += "（来自当前正式目录，知识索引缺失、失效或不可用，暂无文档引用。）"
        lines.append(line)
    if missing:
        lines.append(missing)
    if uncited:
        trace.setdefault("reason", "catalog_index_missing_or_stale")
    elif missing:
        trace["reason"] = "formal_definition_not_found"
    trace["elapsed_ms"] = round((monotonic() - started) * 1000, 3)
    trace["candidates"] = citations
    return {"status": "ok", "answer_zh": "\n".join(lines), "citations": citations,
            "evidence_status": "supported" if citations and not (missing or uncited) else
                               "partial" if citations else "insufficient",
            "uncited_metric_ids": uncited, "retrieval_trace": trace}
