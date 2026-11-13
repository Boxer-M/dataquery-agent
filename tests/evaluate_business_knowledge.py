"""Reconcile catalog and hybrid cases locally; send only the labeled fixture in live mode."""
import argparse
from datetime import date
from hashlib import sha256
import json
import re
from secrets import token_hex
from time import monotonic
from uuid import uuid4

from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.documents import REPOSITORY_SOURCES, catalog_documents, parse_file
from dataquery_agent.rag.embedding import DIMENSIONS, GeminiEmbedding, vector
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_business_planning import reference_connection, reference_rows

ROOT_FIXTURES = ROOT / "tests/fixtures/knowledge"
CASES = json.loads((ROOT_FIXTURES / "rag_v1.json").read_text())["cases"]
TODAY = date(2026, 11, 12)


class FixedEmbedding:
    model = "fixed-ngram-test"
    version = "fixed-ngram-test-768-v1"

    def embed(self, texts, *, query=False):
        result = []
        for text in texts:
            normalized = re.sub(r"\W+", "", text.lower())
            counts = [0] * DIMENSIONS
            for i in range(max(1, len(normalized) - 1)):
                token = normalized[i:i + 2].encode()
                counts[int.from_bytes(sha256(token).digest()[:4], "big") % DIMENSIONS] += 1
            result.append(vector(counts))
        return result


def fixed_model(prompt):
    data = json.loads(prompt.split("\n", 1)[1])
    if "银行卡" in data["question"]:
        return '{"status":"insufficient","claims":[]}'
    for item in data["evidence"]:
        for line in item["text"].splitlines():
            if "七个自然日" in line:
                return json.dumps({"status": "supported", "claims": [{"chunk_id": item["chunk_id"], "quote": line}]})
    return '{"status":"insufficient","claims":[]}'


def truth_for_hybrid(answer):
    results = {}
    with reference_connection() as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        pg.execute("SET LOCAL statement_timeout = '10s'")
        for result in answer["result"]["metric_results"]:
            mid = result["metric_id"]
            request = {"metric_id": mid, "start_date": result["start_date"], "end_date": result["end_date"],
                       "group_by": [], "filters": {}}
            if result["observation_cutoff"]:
                request["observation_cutoff"] = result["observation_cutoff"]
            expected = reference_rows(pg, request)["all"]
            row = result["results"][0]
            from decimal import Decimal
            equal = all((Decimal(str(row[key])) == Decimal(str(expected[key]))) if row[key] is not None
                        and expected[key] is not None else row[key] is expected[key]
                        for key in ("value", "numerator", "denominator") if key in row and key in expected)
            results[mid] = {"expected": expected, "actual": row, "equal": equal}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "live-synthetic"), required=True)
    mode = parser.parse_args().mode
    live = mode == "live-synthetic"
    manager, _ = local_manager()
    backend = manager.generate if live else fixed_model
    embedding = GeminiEmbedding.local() if live else FixedEmbedding()
    reader, admin = KnowledgeStore.local(), KnowledgeStore.local(admin=True)
    owner = token_hex(32)
    service = manager.agent.service
    index = []
    manager.agent.knowledge = KnowledgeService(Retriever(reader, embedding), service.metrics, synthetic=True)
    cid = manager.create_conversation(owner)["conversation_id"]
    report = {"mode": mode, "index": index, "cases": [], "passed": False}
    try:
        if not live:
            for doc in catalog_documents(service):
                if doc.topic in CASES[-1]["topics"]:
                    index.append(admin.index(doc, embedding))
            for source in sorted(REPOSITORY_SOURCES):
                engineering_doc = parse_file(ROOT / source, title=source.split("/")[-1], source_type="technical",
                                             source_uri=source, version="299bbd7")
                index.append(admin.index(engineering_doc, embedding))
        doc = parse_file(ROOT_FIXTURES / "refund_fixture.md", title="测试星店退款规则", source_type="synthetic_fixture",
                         source_uri="fixture:refund_fixture.md", topic="refund_fixture", version="1")
        index.append(admin.index(doc, embedding, owner=owner, external_approved=True))
        for case in CASES:
            if live and case["id"] not in {"policy", "policy_gap"}:
                continue
            calls = []

            def generate(prompt):
                event = {"input": prompt}
                calls.append(event)
                try:
                    reply = backend(prompt)
                except Exception as error:
                    event["error"] = type(error).__name__
                    raise
                event["output"] = reply
                return reply

            manager.generate = generate
            began = monotonic()
            answer = manager.process_message(owner, cid, case["question"], uuid4(), today=TODAY)
            history = manager.get_query(owner, answer["query_id"]) if answer.get("query_id") else None
            result = answer["result"]
            evidence = result.get("knowledge_result", result)
            citations = evidence.get("citations", [])
            candidates = evidence.get("retrieval_trace", {}).get("candidates", [])
            truth = truth_for_hybrid(answer) if case["id"] == "hybrid" and answer["status"] == "ok" else None
            correct = evidence.get("evidence_status") == case["status"]
            if case.get("quote"):
                correct &= any(case["quote"] in item.get("quote", "") for item in citations)
            if case["id"] == "hybrid":
                correct &= bool(truth and all(item["equal"] for item in truth.values()) and len(citations) == 3)
            if case["id"] == "definition":
                correct &= len(citations) == 1 and "没有订单退款率" in answer["answer_zh"]
            citation_ids = {item["chunk_id"] for item in citations}
            returned_ids = {item["chunk_id"] for item in candidates}
            correct &= bool(history and history.get("knowledge", {}).get("citations") == citations and
                            citation_ids <= returned_ids)
            if case["id"] == "definition" or case["id"] == "hybrid":
                expected_topics = case.get("topics", [case.get("topic")])
                expected_locations = {f"#/metrics[metric_id={topic}]" for topic in expected_topics}
                labels = [item["source_location"] in expected_locations for item in candidates]
                recall = len(expected_locations & {item["source_location"] for item in candidates[:6]}) / len(expected_locations)
            elif case["id"] == "policy":
                labels = [item["document_id"] == index[-1]["document_id"] and
                          "可申请条件" in item.get("heading_path", []) for item in candidates]
                recall = float(any(labels[:6]))
            else:
                labels, recall = [], None
            rr = next((1 / (i + 1) for i, matched in enumerate(labels[:6]) if matched), 0) if recall is not None else None
            report["cases"].append({"case": case, "passed": correct, "answer": answer, "history": history,
                                    "ods_truth": truth, "model_calls": calls, "recall_at_6": recall,
                                    "reciprocal_rank_at_6": rr, "citation_count": len(citations),
                                    "verified_citations": len(citation_ids & returned_ids),
                                    "latency_ms": round((monotonic() - began) * 1000)})
        if not live:
            initial = manager.process_message(owner, cid, "商品退款率是怎么定义的？", uuid4(), today=TODAY)
            followup = manager.process_message(owner, cid, "那订单退款率呢？", uuid4(), today=TODAY)
            history = manager.get_query(owner, followup["query_id"])
            followed = (initial["task"] == "knowledge" and followup["task"] == "knowledge"
                        and followup["context_operation"] == "knowledge_followup"
                        and history["knowledge"]["evidence_status"] == "insufficient")
            report["followup"] = {"first": initial, "second": followup, "history": history, "passed": followed}
        report["passed"] = all(item["passed"] for item in report["cases"]) and report.get("followup", {}).get("passed", True)
    except Exception as error:
        report["failure"] = type(error).__name__
    finally:
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
