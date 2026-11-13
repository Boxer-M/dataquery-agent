"""Preflight and evaluate the explicitly authorized repository corpus."""
import argparse
from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from secrets import token_hex
from statistics import mean
from time import monotonic

from dataquery_agent.agent_runtime import AgentRuntime
from dataquery_agent.metadata_service import fingerprint
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.answer import KnowledgeService, build_evidence, validate_answer
from dataquery_agent.rag.documents import (
    CATALOG_PATH, PARSER_VERSION, REPOSITORY_SOURCES, Passage, catalog_documents, parse_file, validate_document,
)
from dataquery_agent.rag.embedding import DIMENSIONS, GeminiEmbedding
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import CHUNKS, DOCS, KnowledgeStore

TODAY = date(2026, 11, 12)
TECHNICAL = {
    "docs/warehouse/METRICS_AND_LINEAGE.md": ((20, 20, "退货观察窗口"),),
    "docs/warehouse/TABLE_DESIGN.md": ((58, 69, "支付尝试事实"), (75, 86, "退货记录事实")),
    "docs/warehouse/QUERY_ASSET_GOVERNANCE.md": ((14, 16, "支付排行资产的参数与验证"),),
}
CASES = (
    ("退货观察截止和下单批次如何影响退货率？", "item_return_rate", CATALOG_PATH, None, "观察截止前"),
    ("退款到账金额是否有正式口径？", "completed_refund_amount", CATALOG_PATH, None, "退款"),
    ("渠道支付尝试排行的候选发布要经过哪些验证？", None,
     "docs/warehouse/QUERY_ASSET_GOVERNANCE.md", "lines:14-16", "精确语义校验"),
    ("dwd.return_record_fact 的 refund_amount 是什么？", None,
     "docs/warehouse/TABLE_DESIGN.md", "lines:75-86", "源退货记录金额"),
    ("明细退货率的全量观察窗口和 as_of 截止是什么？", None,
     "docs/warehouse/METRICS_AND_LINEAGE.md", "lines:20-20", "as_of=2026-01-01"),
)
SENSITIVE = re.compile(
    r"(?:\.env\b|://[^\s/]+:[^\s/@]+@|-----BEGIN|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r"\b(?:sk-|ghp_|AIza)[\w-]{12,}|(?:password|secret|token|密钥|密码)\s*[:=：]|"
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b)", re.I)


def prepare():
    if set(TECHNICAL) != REPOSITORY_SOURCES:
        raise ValueError("repository_allowlist_changed")
    metrics = MetricQueryService(None)
    documents = list(catalog_documents(metrics))
    for path, ranges in TECHNICAL.items():
        full = parse_file(ROOT / path, title=Path(path).stem, source_type="technical", source_uri=path, version="source")
        lines = (ROOT / path).read_text().splitlines()
        passages = tuple(Passage("\n".join(lines[first - 1:last]), (heading,), f"lines:{first}-{last}")
                         for first, last, heading in ranges)
        documents.append(replace(full, passages=passages, version=full.content_hash[:12] + "-reviewed-v1"))
    corpus, manifest = {}, []
    for doc in documents:
        validate_document(doc)
        payloads = ["\n".join([doc.title, *p.heading_path, p.text]) for p in doc.passages]
        if any(SENSITIVE.search(text) for text in payloads):
            raise ValueError("sensitive_document_fragment")
        corpus[doc.source_uri] = payloads
        manifest.append({"source": doc.source_uri, "version": doc.version, "document_hash": doc.content_hash,
                         "chunks": [{"location": p.location, "sha256": sha256(text.encode()).hexdigest(),
                                     "characters": len(text)} for p, text in zip(doc.passages, payloads)]})
    sources = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in (CATALOG_PATH, *TECHNICAL)}
    return metrics, documents, corpus, {"sources": sources, "documents": manifest,
                                        "fragment_count": sum(map(len, corpus.values()))}


class MeasuredEmbedding(GeminiEmbedding):
    def __init__(self, api_key):
        super().__init__(api_key)
        self.events = []
        self.cached = {}

    def embed(self, texts, *, query=False):
        if not query and self.cached:
            return [self.cached[text] for text in texts]
        return super().embed(texts, query=query)

    def record_batch(self, response, *, query, elapsed_ms):
        counts = [getattr(item.statistics, "token_count", None) for item in response.embeddings]
        self.events.append({"query": query, "elapsed_ms": elapsed_ms, "token_counts": counts})


def run(metrics, documents, corpus, report):
    engine = MeasuredEmbedding.local()
    report["embedding_batches"] = engine.events
    report["embedding"] = {"model": engine.model, "version": engine.version, "dimensions": DIMENSIONS,
                           "index_version": fingerprint([PARSER_VERSION, engine.model, engine.version])}
    payloads = [text for parts in corpus.values() for text in parts]
    engine.cached = dict(zip(payloads, engine.embed(payloads), strict=True))
    writer, reader = KnowledgeStore.local(admin=True), KnowledgeStore.local()
    report["index"] = [writer.index(doc, engine) for doc in documents]
    with writer.transaction() as pg:
        private = pg.execute(f"""SELECT c.chunk_id FROM {CHUNKS} c JOIN {DOCS} d USING(document_id)
            WHERE d.source_type='synthetic_fixture' AND c.embedding_version=%s
              AND c.document_version=d.active_version AND c.index_version=d.active_index LIMIT 1""",
                             (engine.version,)).fetchone()
    if private is None:
        raise ValueError("existing_live_private_fixture_required")
    with reader.transaction(token_hex(32), synthetic=True) as pg:
        report["foreign_owner_visible"] = bool(pg.execute(f"SELECT 1 FROM {CHUNKS} WHERE chunk_id=%s",
                                                         (private["chunk_id"],)).fetchone())
    retriever = Retriever(reader, engine)
    evaluated = report["cases"] = []
    for question, topic, source, location, quote in CASES:
        started = monotonic()
        rows, trace = retriever.search(question, today=TODAY, topics=[topic] if topic else None, top_k=6)
        expected_source = source + f"#/metrics[metric_id={topic}]" if topic else source
        rank = next((i for i, row in enumerate(rows, 1) if row["source_uri"] == expected_source and
                     (location is None or row["source_location"] == location)), None)
        evidence, _, _ = build_evidence(rows, metrics.metrics)
        match = next((item for item in evidence if item["source_uri"] == expected_source and
                      (location is None or item["source_location"] == location) and quote in item["evidence_text"]), None)
        citations = []
        if match:
            excerpt = next(line for line in match["evidence_text"].splitlines() if quote in line)
            _, citations = validate_answer(json.dumps({"status": "supported", "claims": [
                {"chunk_id": match["chunk_id"], "quote": excerpt}]}), evidence)
        evaluated.append({"question": question, "expected_source": expected_source, "expected_location": location,
                          "rank": rank, "trace": trace, "citations": citations,
                          "latency_ms": round((monotonic() - started) * 1000)})
    # The standard knowledge route renders formal definitions locally.
    runtime = AgentRuntime(lambda _: (_ for _ in ()).throw(AssertionError("generation is outside this run")))
    result = KnowledgeService(retriever, metrics.metrics).answer(
        CASES[0][0], runtime, today=TODAY, topics=["item_return_rate"])
    report["catalog_answer"] = result
    report["metrics"] = {"recall_at_6": mean(item["rank"] is not None for item in evaluated),
                         "mrr_at_6": mean(1 / item["rank"] if item["rank"] else 0 for item in evaluated),
                         "correct_citations": sum(bool(item["citations"]) for item in evaluated),
                         "expected_citations": len(evaluated)}
    return not report["foreign_owner_visible"] and result["evidence_status"] == "supported" and all(
        item["rank"] and item["citations"] for item in evaluated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-manifest", type=Path)
    args = parser.parse_args()
    metrics, documents, corpus, manifest = prepare()
    if not args.approved_manifest:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if manifest != json.loads(args.approved_manifest.read_text()):
        raise SystemExit("Document contents changed after preflight.")
    report = {"manifest": manifest, "passed": False}
    try:
        report["passed"] = run(metrics, documents, corpus, report)
    except Exception as error:
        report["failure"] = type(error).__name__
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
