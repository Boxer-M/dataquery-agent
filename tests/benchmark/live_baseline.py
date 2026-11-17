"""Live, opt-in baseline using public Golden cases and current production code paths."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values

from dataquery_agent.conversation_context import redact
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.document_ingestion import extract_pdf
from dataquery_agent.rag.documents import Document, Passage, parse_file
from dataquery_agent.rag.pdf_review import approve, build_document
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_agent_e2e import FIXTURES, GOLDEN, TODAY, evaluate_capture, reference_connection
from evaluate_integrated_e2e import EXTRA, PDF, check_extra
from evaluate_repository_embeddings import prepare

from .adapters import live_environment, snapshot, trace_fields
from .dataset import DATASET, ROOT, digest
from .evidence_review import REPORT as REVIEW, assemble
from .expansion import compare_rows, read, sql_label, write
from .live_baseline_budget import Budget, BaselineEmbedding, LIMITS, estimate, generator
from .live_baseline_scores import expansion_checks, retrieval, summarize
from .runner import git

VERSION = "real-gemini-baseline-v2"
REPORT = ROOT / "reports/benchmark/real_gemini_baseline_v2.json"
TECH_SOURCE = "docs/warehouse/TABLE_DESIGN.md"
TECH_RANGES = {(3, 6), (23, 28), (71, 72), (88, 89)}


def inventory(*, authorized_technical=False):
    reviewed = assemble(read(REVIEW))
    old = {row["case_id"]: row for row in read(DATASET)["cases"]}
    fixture = [(old[f"agent_e2e_v1:{row['id']}"], row, "legacy") for row in GOLDEN["cases"]]
    fixture += [(old[f"integrated_e2e_v1:{row['id']}"], row, "integrated") for row in EXTRA]
    public = [(case, None, "expansion") for case in reviewed["cases"] if case["review_status"] == "reviewed"]
    _, documents, _, local_manifest = prepare()
    authorized_manifest = read(ROOT / "reports/warehouse/repository_embeddings_v1.json")["manifest"]
    if local_manifest["documents"] != authorized_manifest["documents"]:
        raise ValueError("authorized repository manifest differs from checked-in corpus")
    approved = {(doc.title, part.text) for doc in documents for part in doc.passages}
    selected = {case["case_id"] for case, _, _ in fixture}
    excluded = [{"case_id": case["case_id"], "reason": (
        "legacy_pending" if case["review_status"] == "needs_review" else
        "no_independently_reviewed_live_label_mapping")}
        for case in old.values() if case["case_id"] not in selected]
    excluded += [{"case_id": row["case_id"], "reason": "needs_review"}
                 for row in reviewed["cases"] if row["review_status"] == "needs_review"]
    runnable = list(fixture)
    for case, spec, family in public:
        if case["task_category"] == "rag" and not authorized_technical:
            source = case["golden"]["evidence"]
            if not any(doc.source_uri == source["source"] and any(source["quote"] in part.text for part in doc.passages)
                       for doc in documents):
                excluded.append({"case_id": case["case_id"], "reason": "relevant_passage_outside_authorized_corpus"})
                continue
        runnable.append((case, spec, family))
    runnable.sort(key=lambda item: bool(item[1] and item[1].get("withdraw")))
    return runnable, excluded, approved, reviewed


def technical_fixture(reviewed):
    from hashlib import sha256

    raw = (ROOT / TECH_SOURCE).read_bytes()
    lines = raw.decode().splitlines()
    content_hash = sha256(raw).hexdigest()
    evidence = [case["golden"]["evidence"] for case in reviewed["cases"] if case["task_category"] == "rag"]
    passages = []
    for first, last in sorted(TECH_RANGES):
        location = f"lines:{first}-{last}"
        targets = [item for item in evidence if item["chunk"]["location"] == location]
        text = "\n".join(lines[first - 1:last]).strip()
        if not targets or not all(item["source"] == TECH_SOURCE and item["content_hash"] == content_hash
                                  and item["quote"] in text for item in targets):
            raise ValueError("approved source changed")
        passages.append(Passage(text, (), location))
    return Document("表清单与字段级设计", "synthetic_fixture", "fixture:baseline-table-design",
                    content_hash, content_hash[:12] + "-baseline-v2", tuple(passages))


def publish_fixtures(stack, writer, embedding, approved, owner, technical):
    policy = parse_file(FIXTURES / "knowledge/refund_fixture.md", title="测试星店退款规则", source_type="synthetic_fixture",
                        source_uri="fixture:refund_fixture.md", topic="refund_fixture", version="1")
    extraction = extract_pdf(PDF, owner=owner)
    receipt = approve(extraction, PDF, owner=owner, slug="baseline-" + token_hex(8),
                      title="Synthetic PDF verification", basis="Checked the authorized repository sample", corrections={})
    pdf = build_document(receipt["review"])
    ids = []
    for doc in (policy, pdf, technical):
        if doc is None:
            ids.append(None)
            continue
        approved.update((doc.title, part.text) for part in doc.passages)
        embedding.approved_documents.update("\n".join([doc.title, *part.heading_path, part.text]) for part in doc.passages)
        try:
            result = (writer.index(doc, embedding, owner=owner, external_approved=True) if doc is not pdf else
                      writer.index(None, embedding, owner=owner, external_approved=True, pdf_receipt=receipt, pdf=PDF))
        except Exception as error:
            if "embedding" not in embedding.budget.unavailable and getattr(error, "code", None) not in {429, 503}:
                raise
            ids.append(None)
        else:
            ids.append(result["document_id"])
            stack.callback(writer.delete, result["document_id"], owner=owner)
    return ids


def fixture_checks(family, spec, answer, history, manager, owner, pg, outputs, candidates):
    if family == "legacy":
        checks, _, actual, _ = evaluate_capture(spec, answer, history, outputs, manager.agent.service, pg)
        row = {"intent_accuracy": checks["intent"], "entities": [spec["ids"], actual["metric_ids"]],
               "plan_validity": checks["legal_plan"], "tool_route_accuracy": checks["route"],
               "sql_result_accuracy": checks["sql_numeric"], "ods_consistency": checks["sql_numeric"],
               "citation_validity": checks["catalog_citation"], "guardrail": checks["guardrail"]}
        if spec["check"] == "synthetic_policy":
            row["retrieval"] = retrieval(answer, candidates["policy"])
            quotes = answer["result"].get("citations", [])
            if quotes:
                row["citation_validity"] = all(c["chunk_id"] in candidates["policy"] and c["quote"] in candidates["policy"][c["chunk_id"]]
                                               for c in quotes)
    else:
        checks, _ = check_extra(spec, answer, history, manager, owner, pg)
        row = {"sql_result_accuracy": checks.get("sql_numeric"), "ods_consistency": checks.get("sql_numeric"),
               "citation_validity": checks.get("pdf_citation", checks.get("catalog_citation")),
               "guardrail": checks.get("guardrail") if spec["check"] == "absent" else None,
               "plan_validity": checks.get("dag")}
        if spec["check"] in {"pdf", "combined"}:
            row["retrieval"] = retrieval(answer, candidates[spec["page"]])
    return row | {"task_success": all(value for value in checks.values() if value is not None),
                  "checks": checks}


def failure_category(answer, row, events, blocked):
    checks = row.get("checks", {})
    if row["task_success"]:
        return None
    if blocked:
        return blocked[-1]["reason"]
    if any(event["status"] == "error" for event in events) and answer.get("code") in {
        "model_unavailable", "retrieval_unavailable", "embedding_unavailable", "invalid_model_output",
    }:
        return "external_model_service"
    if answer.get("code") in {"unsupported_grouping", "unsupported_filter", "outside_verified_window"}:
        return "capability_rejection"
    if row.get("retrieval") and not set(row["retrieval"][0]).intersection(row["retrieval"][1][:6]):
        return "retrieval_miss"
    if row.get("citation_validity") is False:
        return "citation_invalid"
    if row.get("plan_validity") is False:
        return "plan_generation"
    if row.get("sql_result_accuracy") is False:
        return "sql_result"
    if row.get("intent_accuracy") is False or row.get("entities") and set(row["entities"][0]) != set(row["entities"][1]):
        return "intent_or_entity"
    if checks.get("rag_answer") is False:
        return "answer_lacks_evidence"
    return "context" if answer.get("status") == "clarify" else "other_execution"


def execute_case(case, spec, family, manager, owner, conversation, pg, budget, generate, blocked, calls, candidates):
    before = len(budget.events)
    blocked.clear()
    calls.clear()
    outputs = []

    def record(prompt):
        try:
            result = generate(prompt)
        except Exception as error:
            outputs.append({"error": type(error).__name__})
            raise
        outputs.append({"model_output": redact(result["text"] or "")})
        return result

    manager.generate = record
    started = monotonic()
    answer = manager.process_message(owner, conversation, case["question"], uuid4(), today=TODAY)
    latency = round((monotonic() - started) * 1000)
    events = budget.events[before:]
    history = manager.get_query(owner, answer["query_id"]) if answer.get("query_id") else None
    trace = answer["trace"]
    if family == "expansion":
        row = expansion_checks(case, answer, calls, history, pg, manager.agent.knowledge.retriever.store, owner)
        if case["task_category"] == "rag":
            evidence = case["golden"]["evidence"]
            relevant = {item["chunk_id"]: item["chunk_text"] for item in candidates["technical"]
                        if evidence["quote"] in item["chunk_text"]}
            if not relevant:
                raise ValueError("relevant approved source chunk missing")
            row["retrieval"] = retrieval(answer, relevant)
            citations = answer["result"].get("citations", [])
            row["citation_validity"] = bool(citations) and all(
                item["chunk_id"] in relevant and item["quote"] in relevant[item["chunk_id"]] for item in citations)
            row["task_success"] &= row["citation_validity"]
    else:
        row = fixture_checks(family, spec, answer, history, manager, owner, pg, outputs, candidates)
    category = failure_category(answer, row, events, blocked)
    interrupted = (any(event.get("reason") == "provider_region_unavailable" for event in events)
                   or any(event["reason"] == "provider_region_unavailable" for event in blocked))
    if interrupted:
        row = {"task_success": None, "scoring_disposition": "provider_region_unavailable"}
        category = "external_model_service"
    latency_fields = trace_fields(trace, mode="gemini", elapsed=latency)
    latency_fields["model_calls"] = sum(event["kind"] == "generation" for event in events)
    return {key: case[key] for key in ("case_id", "split", "semantic_group_id", "template_id", "task_category")} | {
        **row, **latency_fields, "task_type": ("multi_turn" if spec and spec.get("after") else case["task_category"]),
        "embedding_calls": sum(event["kind"] == "embedding" for event in events), "api_events": events,
        "api_cost": estimate(events), "retries": sum(item.get("attempt", 0) > 1 for item in trace["model_attempts"]),
        "error_category": category,
        "trace_id": trace.get("trace_id"), "status": answer["status"], "code": answer.get("code"),
        "query_id": answer.get("query_id"), "blocked_attempts": list(blocked),
        "phase_ms": {phase: sum(values) if values else None for phase, values in {
            "planner_model": [e["elapsed_ms"] for e in trace["model_attempts"] if e["stage"] == "planning"],
            "answer_model": [e["elapsed_ms"] for e in trace["model_attempts"] if e["stage"] == "knowledge"],
            "sql_tools": [e["elapsed_ms"] for e in trace["tools"] if e.get("request") and "elapsed_ms" in e],
            "rag_retrieval": [e["elapsed_ms"] for e in trace["tools"] if e.get("scope") and "elapsed_ms" in e],
        }.items()},
        "model_attempts": trace["model_attempts"], "tool_trace": [{key: tool[key] for key in
            ("kind", "tool", "status", "error_code", "trace_id", "plan_id", "task_id", "attempt_id",
             "capability_trace_id", "request", "strategy", "scope") if key in tool} for tool in trace["tools"]],
        "citation_locations": [{key: c.get(key) for key in ("source_uri", "document_version", "chunk_id", "source_location")}
                               for c in answer["result"].get("knowledge_result", answer["result"]).get("citations", [])],
        "observed_metric_rows_sha256": digest([item.get("results") for _, item in calls]),
    }


def run(path, *, authorized_technical):
    cases, excluded, approved, public = inventory(authorized_technical=authorized_technical)
    config = dotenv_values(ROOT / ".env")
    budget = Budget(config.get("GEMINI_MODEL", ""))
    model_environment = live_environment()
    manager, _ = local_manager()
    owner, other = token_hex(32), token_hex(32)
    embedding = BaselineEmbedding(config["GEMINI_API_KEY"], budget)
    manager.agent.knowledge.retriever.embedding = embedding
    writer = KnowledgeStore.local(admin=True)
    calls, blocked, rows, conversations = [], [], [], {}
    generate = generator(manager.generate, budget, approved, blocked)
    query = manager.agent.service.query

    def capture_query(request):
        result = query(request)
        calls.append((request.copy(), result))
        return result

    report = {"version": VERSION, "mode": "real_gemini", "git_commit": git("rev-parse", "HEAD"),
              "started_at": datetime.now(timezone.utc).isoformat(), "dataset_hash": digest(public),
              "dataset_version": public["dataset_version"],
              "model_environment": model_environment, "budgets": LIMITS, "excluded": excluded, "cases": rows,
              "fixed_proposal_cases": 0, "replay_cases": 0}
    with ExitStack() as stack, reference_connection() as pg:
        report["ods_snapshot"] = snapshot(pg)
        tech = technical_fixture(public) if authorized_technical else None
        policy_id, pdf_id, tech_id = publish_fixtures(stack, writer, embedding, approved, owner, tech)
        report["setup_api_events"] = list(budget.events)
        report["authorized_technical_ranges"] = sorted(TECH_RANGES) if tech else []
        write(path, report)
        policy = {chunk["chunk_id"]: chunk["chunk_text"] for chunk in (writer.chunks(policy_id, owner=owner) if policy_id else [])
                  if "七个自然日" in chunk["chunk_text"]}
        pdf = writer.chunks(pdf_id, owner=owner) if pdf_id else []
        tech_rows = writer.chunks(tech_id, owner=owner) if tech_id else []
        candidates = {"policy": policy} | {page: [item["chunk_id"] for item in pdf
                                                  if item["source_location"].startswith(f"page:{page};")]
                                         for page in (1, 2)}
        with patch.object(manager.agent.service, "query", capture_query):
            for case, spec, family in cases:
                task = case["task_category"]
                needs_rag = (spec["check"] in {"pdf", "combined", "absent", "synthetic_policy", "insufficient_evidence"}
                             if spec else task in {"rag", "pdf"})
                if needs_rag and not (policy_id and pdf_id and (tech_id or task != "rag" or spec)):
                    excluded.append({"case_id": case["case_id"], "reason": "embedding_setup_unavailable"})
                    continue
                user = other if spec and spec.get("foreign") else owner
                if spec and spec.get("withdraw"):
                    writer.delete(pdf_id, owner=owner)
                parent = spec.get("after") if spec else None
                conversation = conversations[parent] if parent else manager.create_conversation(user)["conversation_id"]
                if spec:
                    conversations[spec["id"]] = conversation
                manager.agent.knowledge.synthetic = bool(spec and spec["check"] in {"synthetic_policy", "insufficient_evidence"}
                                                         or not spec and task == "rag")
                candidates["technical"] = tech_rows
                try:
                    rows.append(execute_case(case, spec, family, manager, user, conversation, pg, budget,
                                             generate, blocked, calls, candidates))
                except Exception as error:
                    rows.append({"case_id": case["case_id"], "task_type": case["task_category"],
                                 "task_success": False, "error_category": "other_execution",
                                 "error_type": type(error).__name__})
                write(path, report)
    with reference_connection() as pg:
        if snapshot(pg) != report["ods_snapshot"]:
            raise ValueError("ODS snapshot changed during baseline")
    report.update(api_cost=estimate(budget.events),
                  status="partial_provider_region_unavailable" if budget.unavailable else "executed",
                  excluded_reasons=dict(Counter(r["reason"] for r in excluded)),
                  api_calls=dict(Counter(e["kind"] for e in budget.events)),
                  provider_failures=dict(Counter(str(e.get("http_status") or e.get("error_type"))
                                                 for e in budget.events if e["status"] == "error")),
                  reservation={"tokens": budget.reserved_tokens, "usd": round(budget.reserved_usd, 6)},
                  completed_at=datetime.now(timezone.utc).isoformat())
    finalize(report, cases)
    write(path, report)


def finalize(report, cases):
    """Keep upstream-outage dependencies and deterministic results separately visible."""
    indexed = {row["case_id"]: row for row in report["cases"]}
    for row in indexed.values():
        if row.get("ods_consistency") is False and not any(
            tool.get("request") for tool in row.get("tool_trace", [])
        ):
            row["ods_consistency"] = None
    parents = {spec["id"]: case["case_id"] for case, spec, _ in cases if spec}
    for case, spec, _ in cases:
        row = indexed.get(case["case_id"])
        if row and spec and spec.get("after"):
            prior = indexed.get(parents[spec["after"]], {})
            if prior.get("scoring_disposition") == "provider_region_unavailable":
                for key in ("sql_result_accuracy", "ods_consistency", "task_success", "checks"):
                    row[key] = None
                row.update(scoring_disposition="upstream_provider_region_unavailable",
                           error_category="external_model_service_dependency")
    rows = report["cases"]
    report["summary"] = summarize(rows, report["excluded"])
    report["cohorts"] = {
        "deterministic_scored": summarize([r for r in rows if r.get("model_calls") == 0
                                           and r.get("embedding_calls") == 0
                                           and r.get("task_success") is not None], []),
        "gemini_response_received": summarize([r for r in rows if any(
            e["kind"] == "generation" and e["status"] == "ok" for e in r.get("api_events", []))], []),
    }


def verify_sql_file(path):
    """Independently verify the frozen SQL labels even if natural language is blocked."""
    from dataquery_agent.metric_query_service import MetricQueryService

    report = read(path)
    cases, _, _, public = inventory(authorized_technical=bool(report["authorized_technical_ranges"]))
    if digest(public) != report["dataset_hash"]:
        raise ValueError("report belongs to a different dataset")
    report["dataset_version"] = public["dataset_version"]
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
    verified = []
    with reference_connection() as pg:
        mark = snapshot(pg)
        if mark != report["ods_snapshot"]:
            raise ValueError("report belongs to a different ODS snapshot")
        for case, _, _ in cases:
            proof = case["golden"]["sql"]
            if not proof or "calls" not in proof:
                continue
            actual = sql_label(pg, service, [call["parameters"] for call in proof["calls"]], mark)
            if not all(compare_rows(a["expected_result"], b["expected_result"]) for a, b in zip(
                    proof["calls"], actual["calls"], strict=True)):
                raise ValueError("frozen SQL truth mismatch")
            verified.append({"case_id": case["case_id"], "calls": actual["calls"]})
    report["offline_sql_validation"] = {"snapshot": mark["id"], "cases": verified,
                                        "case_count": len(verified), "call_count": sum(len(r["calls"]) for r in verified)}
    finalize(report, cases)
    write(path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-model", action="store_true")
    parser.add_argument("--allow-four-reviewed-technical-spans", action="store_true")
    parser.add_argument("--out", type=Path, default=REPORT)
    parser.add_argument("--verify-sql", action="store_true", help="Append local read-only SQL reconciliation")
    args = parser.parse_args()
    if args.verify_sql:
        verify_sql_file(args.out)
        return
    if not args.allow_live_model:
        parser.error("explicit --allow-live-model required")
    if args.out.exists():
        parser.error("choose a fresh report path")
    run(args.out, authorized_technical=args.allow_four_reviewed_technical_spans)


if __name__ == "__main__":
    main()
