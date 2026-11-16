"""Extend the golden E2E evaluation with live conversation, chart and PDF checks."""
from collections import Counter
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
import json
from math import ceil
from pathlib import Path
from secrets import token_hex
from time import monotonic
from uuid import uuid4

from dataquery_agent.api_models import public_response
from dataquery_agent.conversation_context import redact
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.metric_query_service import windows
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.documents import parse_file
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.pdf_review import approve, build_document
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_agent_e2e import FIXTURES, GOLDEN, TODAY, reference_connection, reference_rows, rescore, safe_generate

EXTRA = json.loads((FIXTURES / "integrated_e2e_v1.json").read_text())
PDF = ROOT / "output/pdf/document-extraction-sample.pdf"


def check_extra(case, answer, history, manager, owner, pg):
    response = public_response(answer)
    mode = case["check"]
    result, trace = answer["result"], answer["trace"]
    citations = response.citations
    verification = {"history": bool(history and response.trace == public_response(history, history=True).trace
                                    and citations == public_response(history, history=True).citations)}
    expected = {}
    if mode in {"scalar", "month", "channel"}:
        expected_request = {
            "metric_id": case.get("metric", "order_count"),
            "start_date": case.get("start", "2025-01-01"),
            "end_date": case.get("end", "2026-01-01" if mode == "month" else "2025-02-01"),
            "group_by": [mode] if mode == "channel" else [], "filters": {},
        }
        if mode == "month":
            expected = {start.strftime("%Y-%m"): reference_rows(pg, expected_request | {
                "start_date": start.isoformat(), "end_date": end.isoformat()})["all"]["value"]
                for start, end in windows(date(2025, 1, 1), date(2026, 1, 1), True)}
        else:
            expected = {key: row["value"] for key, row in reference_rows(pg, expected_request).items()}
        actual = {row["month" if mode == "month" else "bucket"]: Decimal(str(row["value"]))
                  for row in response.rows if row.get("value") is not None}
        verification["sql_numeric"] = response.status == "completed" and actual == expected
        requests = [tool for tool in trace["tools"] if "request" in tool]
        expected_request["group_by"] = [mode] if mode in {"month", "channel"} else []
        verification["sql_provenance"] = len(requests) == 1 and (
            {"filters": {}, "group_by": []} | requests[0]["request"]) == expected_request and any(
            route["status"] == "STANDARD_METRIC" and route["trace_id"] == requests[0]["capability_trace_id"]
            and set(requests[0]["sources"]) == route["required_fields"].keys() for route in answer["routes"])
        if mode != "scalar":
            chart = response.visualization
            verification["visualization"] = bool(chart and chart.dimension.semantic == mode
                and chart.window.start_date == expected_request["start_date"]
                and chart.window.end_date == expected_request["end_date"]
                and set(chart.row_order) == set(range(len(expected))))
    elif mode == "pdf":
        valid = manager.agent.knowledge.retriever.store.current_pdf_citations(result.get("citations", []), owner=owner)
        matching = [item for item in citations if item.source_type == "reviewed_pdf"]
        verification["pdf_citation"] = len(valid) == len(matching) == 1 and (
            matching[0].page_number, matching[0].quote, matching[0].source_hash
        ) == (case["page"], case["quote"], result["citations"][0]["source_hash"])
        verification["pdf_citation"] &= bool(trace["tools"] and trace["tools"][0].get("task_id")
                                             and trace["tools"][0].get("scope") == "reviewed_pdf")
    elif mode == "catalog":
        definition = manager.agent.service.metrics["order_count"]["definition"]
        verification["catalog_citation"] = len(citations) == 1 and (
            citations[0].quote, citations[0].source_type, citations[0].document_version
        ) == (definition, "catalog", manager.agent.service.catalog["catalog_version"])
        verification["guardrail"] = not any(item.get("query_embedding_called") for item in trace["tools"])
    elif mode == "combined":
        verification["sql_plus_pdf"] = bool(citations) and any(c.source_type == "reviewed_pdf" for c in citations) and bool(response.rows)
        verification["guardrail"] = (answer["code"] == "sql_pdf_combination_unsupported"
                                     and not trace["tools"] and not trace["model_attempts"])
    else:
        verification["guardrail"] = response.evidence_status == "insufficient" and not citations and not trace["model_attempts"]
    return verification, expected


def capture(case, manager, owner, conversation, embedding, backend, source):
    outputs = []
    manager.generate = safe_generate(backend, outputs, source, case["question"])
    begin = len(embedding.batches)
    started = monotonic()
    answer = manager.process_message(owner, conversation, case["question"], uuid4(), today=TODAY)
    ms = round((monotonic() - started) * 1000)
    history = manager.get_query(owner, answer["query_id"]) if answer.get("query_id") else None
    return {"golden": case, "answer": answer, "history": history, "model_outputs": outputs,
            "embedding_batches": embedding.batches[begin:], "elapsed_ms": ms}


def compact(item):
    answer, history = item["answer"], item["history"]
    response = public_response(answer)
    return {key: item[key] for key in ("golden", "elapsed_ms", "scores", "passed", "failure",
                                      "embedding_batches", "model_outputs", "ods")} | {
        "query_id": response.query_id, "status": response.status, "code": answer["code"],
        "answer": redact(response.answer or ""), "evidence_status": response.evidence_status,
        "trace": response.trace.model_dump(exclude_none=True),
        "model_attempts": answer["trace"]["model_attempts"],
        "citations": [c.model_dump(exclude_none=True) for c in response.citations],
        "history_path": (history or {}).get("execution_strategy"),
    }


def summarize(results):
    groups = Counter(case["passed"] for case in results)
    elapsed = sorted(case["elapsed_ms"] for case in results)
    attempts = [attempt for case in results for attempt in case["model_attempts"]]
    checks = {}
    for case in results:
        for key, passed in case["scores"].items():
            if passed is not None:
                row = checks.setdefault(key, {"passed": 0, "total": 0})
                row["passed"] += passed
                row["total"] += 1
    return {"passed": groups[True], "total": len(results), "checks": checks,
            "answer_model_calls": len(attempts),
            "embedding_calls": sum(len(case["embedding_batches"]) for case in results),
            "tokens": {key: sum(item.get("usage", {}).get(key, 0) for item in attempts)
                       for key in ("prompt", "output", "total")},
            "latency_ms": {name: elapsed[ceil(percent * len(elapsed)) - 1] if elapsed else None
                           for name, percent in (("p50", .50), ("p95", .95))},
            "failures": dict(Counter(case["failure"] for case in results if not case["passed"]))}


def run():
    manager, _ = local_manager()
    backend = manager.generate
    owner, other = token_hex(32), token_hex(32)
    writer, embedding = KnowledgeStore.local(admin=True), MeasuredEmbedding.local()
    manager.agent.knowledge.retriever.embedding = embedding
    text = parse_file(FIXTURES / "knowledge/refund_fixture.md", title="测试星店退款规则",
                      source_type="synthetic_fixture", source_uri="fixture:refund_fixture.md",
                      topic="refund_fixture", version="1")
    with ExitStack() as cleanup, reference_connection() as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        pg.execute("SET LOCAL statement_timeout = '15s'")
        existing = writer.index(text, embedding, owner=owner, external_approved=True)
        cleanup.callback(writer.delete, existing["document_id"], owner=owner)
        pdf_id, pdf_source = stage_pdf(writer, embedding, owner, cleanup)
        setup_batches = list(embedding.batches)
        conversations, base, added = {}, [], []
        for case in GOLDEN["cases"] + EXTRA:
            case_owner = other if case.get("foreign") else owner
            if case.get("withdraw"):
                assert writer.delete(pdf_id, owner=owner)
            conversation = (conversations[case["after"]] if "after" in case else
                            manager.create_conversation(case_owner)["conversation_id"])
            conversations[case["id"]] = conversation
            manager.agent.knowledge.synthetic = case["check"] in {"synthetic_policy", "insufficient_evidence"}
            source = pdf_source if "PDF" in case["question"] else text
            item = capture(case, manager, case_owner, conversation, embedding, backend, source)
            if case in GOLDEN["cases"]:
                base.append(item)
            else:
                scores, ods = check_extra(case, item["answer"], item["history"], manager, case_owner, pg)
                failure = next((key for key, passed in scores.items() if not passed), None)
                if failure == "sql_plus_pdf" and scores["guardrail"]:
                    failure = item["answer"]["code"]
                item.update(scores=scores, ods=ods, failure=failure, passed=failure is None)
                added.append(item)
        rescore({"cases": base}, pg)
        results = [compact(item) for item in base + added]
    return {"mode": "live_pipeline", "setup_embedding_batches": setup_batches, "summary": summarize(results),
            "real_answer_model_samples": summarize([case for case in results if case["model_attempts"]]),
            "cases": results}


def stage_pdf(writer, embedding, owner, cleanup):
    extraction = extract_pdf(PDF, owner=owner)
    receipt = approve(extraction, PDF, owner=owner, slug="integrated-" + token_hex(8),
                      title="Synthetic PDF verification", basis="Compared with the synthetic original", corrections={})
    published = writer.index(None, embedding, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=PDF)
    cleanup.callback(writer.delete, published["document_id"], owner=owner)
    return published["document_id"], build_document(receipt["review"])


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, default=str, indent=2))
