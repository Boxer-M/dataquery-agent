"""Golden-set evaluation of real Gemini, the existing Agent, and independent ODS."""
import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import json
from math import ceil
from pathlib import Path
from secrets import token_hex
import os
from time import monotonic
from uuid import uuid4

from dataquery_agent.agent_prompts import PROMPT_VERSIONS
from dataquery_agent.conversation_context import redact
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.business_plan import parse_proposal
from dataquery_agent.intent_semantic_resolver import SemanticResolver, parse_intent
from dataquery_agent.metric_query_service import MetricQueryService
from dataquery_agent.rag.answer import KnowledgeService
from dataquery_agent.rag.documents import parse_file
from dataquery_agent.rag.embedding import GeminiEmbedding
from dataquery_agent.rag.retrieval import Retriever
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_business_planning import reference_connection, reference_rows

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = json.loads((FIXTURES / "agent_e2e_v1.json").read_text())
TODAY = date.fromisoformat(GOLDEN["today"])


def numeric_check(case, result, calls, pg):
    month = case.get("month", 1)
    expected_ids = case["ids"]
    expected_request = {"start_date": f"2025-{month:02d}-01", "end_date": f"2025-{month+1:02d}-01",
                        "group_by": [], "filters": {}}
    calls = [{"group_by": [], "filters": {}} | call for call in calls]
    if calls != [expected_request | {"metric_id": mid} for mid in expected_ids]:
        return False, {}
    if "query_result" in result:
        rows = result["query_result"]["results"]
        values = {expected_ids[0]: rows[0]["value"]} if len(rows) == 1 and rows[0]["bucket"] == "all" else {}
    else:
        rows = result.get("results", [])
        values = {mid: cell["value"] for mid, cell in rows[0]["metrics"].items() if cell} if (
            len(rows) == 1 and rows[0]["bucket"] == "all") else {}
    expected = {req["metric_id"]: reference_rows(pg, req)["all"]["value"] for req in calls}
    return ({mid: Decimal(str(value)) for mid, value in values.items() if value is not None} == expected), expected


def safe_generate(backend, records, fixture, question, *, joint=None):
    """Reuse the approved synthetic document only, plus existing public planning inputs."""
    def generate(prompt):
        stage = prompt.split("]", 1)[0].removeprefix("[")
        record = {"prompt_version": stage}
        records.append(record)
        if stage.startswith("knowledge-"):
            payload = json.loads(prompt.split("\n", 1)[1])
            texts = {chunk.text for chunk in fixture.passages}
            if payload["question"] != (joint[1] if joint else question) or not all(item["text"] in texts for item in payload["evidence"]):
                record["error"] = "external_scope_rejected"
                raise ValueError("external_scope_rejected")
        elif (stage not in {"intent-v3", PROMPT_VERSIONS["planning"]} or
              not all(fragment in prompt for fragment in (joint or (question.split("，并给出正式定义")[0],)))):
            record["error"] = "external_scope_rejected"
            raise ValueError("external_scope_rejected")
        try:
            output = backend(prompt)
        except Exception as error:
            record["error"] = type(error).__name__
            raise
        record["model_output"] = redact(output.get("text") or "")
        return output
    return generate


def check(case, answer, history, model_outputs, service, pg):
    result, trace = answer["result"], answer["trace"]
    tools = trace["tools"]
    calls = [item["request"] for item in tools if "request" in item]
    sql_sources = all(item.get("status") == "ok" and item["trace_id"] and any(
        route.get("trace_id") == item.get("capability_trace_id") and
        set(item["sources"]) == route["required_fields"].keys() for route in answer["routes"])
        for item in tools if "request" in item)
    if any("task_id" in item and "request" in item for item in tools):
        from dataquery_agent.capability_trace import BINDING_FIELDS, capability_link
        records = trace.get("capability_records", [])
        saved = history or {}
        persisted_tools = {item.get("trace_id"): item for item in saved.get("tool_traces", [])}
        sql_sources = sql_sources and records == saved.get("capability_records") and all(
            capability_link(item, records) is not None
            and all(persisted_tools.get(item["trace_id"], {}).get(key) == item.get(key)
                    for key in (*BINDING_FIELDS, "capability_trace_id", "request", "sources", "status"))
            and capability_link(persisted_tools.get(item["trace_id"], {}), records) is not None
            for item in tools if "task_id" in item and "request" in item
        )
    knowledge = result.get("knowledge_result", result)
    citations = knowledge.get("citations", [])
    strategy = knowledge.get("retrieval_trace", {}).get("strategy")
    path = "+".join(filter(None, ["metric_query_service" if calls else None,
                                 "rag" if strategy and strategy.startswith("pgvector") else strategy]))
    if not path:
        path = {"unsupported": "reject", "clarify": "clarify"}.get(answer["status"])
    resolution = result.get("metric_resolution", {})
    ids = (resolution.get("metric_ids") or [call["metric_id"] for call in calls]
           or (answer.get("match") or {}).get("confirmed") or [item["metric_id"] for item in answer["choices"]])
    # Even rejected plans can provide observable, schema-validated metric proposals.
    for record in model_outputs:
        try:
            candidate = json.loads(record["model_output"])
        except (KeyError, ValueError):
            continue
        if not ids and isinstance(candidate, dict) and isinstance(candidate.get("metric_ids"), list):
            ids = candidate["metric_ids"]
    status_ok = (answer["status"], answer["code"]) == (case["status"], case.get("code"))
    scores = {"intent": answer["task"] == case["intent"], "entity": sorted(ids) == sorted(case["ids"]),
              "route": path == case["path"], "sql_provenance": sql_sources if calls else None, "status": status_ok,
              "legal_plan": None, "sql_numeric": None, "catalog_citation": None, "guardrail": None,
              "rag_answer": None, "history": bool(history)}
    if case.get("stage") == "planning" and case["status"] == "ok":
        plan = result.get("plan")
        scores["legal_plan"] = bool(plan and plan["metric_ids"] == case["ids"] and plan["analysis_type"] == "summary"
                                    and [item["request"] for item in plan["required_tool_calls"]] == calls)
    mode, expected = case["check"], {}
    if "ods" in mode:
        scores["sql_numeric"], expected = numeric_check(case, result, calls, pg)
    if "catalog" in mode:
        scores["catalog_citation"] = (
            [item["source_uri"].split("metric_id=")[-1].rstrip("]") for item in citations] == case["ids"]
            and all(item["quote"] == service.metrics[mid]["definition"]
                    and item["document_version"] == service.catalog["catalog_version"] and item["chunk_id"]
                    and item["source_type"] == "catalog" for mid, item in zip(case["ids"], citations))
            and bool(history and history["knowledge"]["citations"] == citations)
        )
    if mode in {"no_execution", "missing_definition"}:
        scores["guardrail"] = status_ok and not calls and not citations and (
            mode != "missing_definition" or ("未登记" in answer["answer_zh"] and ids == []))
    if mode == "synthetic_policy":
        scores["rag_answer"] = knowledge.get("evidence_status") == "supported" and bool(citations) and all(
            item["source_type"] == "synthetic_fixture" and "七个自然日" in item["quote"] for item in citations)
    if mode == "insufficient_evidence":
        scores["rag_answer"] = knowledge.get("evidence_status") == "insufficient" and not citations
    return scores, expected, {"path": path, "metric_ids": ids, "status": answer["status"], "code": answer["code"]}


def summarize(cases):
    attempts = [item for case in cases for item in case["answer"]["trace"]["model_attempts"]]
    llm = [case for case in cases if case["answer"]["trace"]["model_attempts"]]
    def scores(rows):
        return {key: {"passed": sum(case["scores"][key] is True for case in rows),
                      "total": sum(case["scores"][key] is not None for case in rows)}
                for key in cases[0]["scores"]}
    times = sorted(case["elapsed_ms"] for case in cases)
    return {"samples": len(cases), "passed": sum(case["passed"] for case in cases),
            "llm_samples": len(llm), "llm_sample_scores": scores(llm),
            "deterministic_sample_scores": scores([case for case in cases if case not in llm]),
            "scores": scores(cases), "model_calls": len(attempts),
            "received_model_outputs": sum(event["status"] in {"received", "invalid_output"} for event in attempts),
            "tokens": {key: sum(event.get("usage", {}).get(key, 0) for event in attempts)
                       for key in ("prompt", "output", "total") if any(key in event.get("usage", {}) for event in attempts)},
            "latency_ms": {"p50": times[ceil(.50 * len(times)) - 1], "p95": times[ceil(.95 * len(times)) - 1]},
            "failure_categories": dict(Counter(case["failure"] for case in cases if case["failure"]))}


def score_model_proposals(report):
    names = SemanticResolver(MetricQueryService(None)).names
    for case in report["cases"]:
        golden = case["golden"]
        stage = golden.get("stage")
        observed_intent, observed_ids = None, []
        applicable = stage in {"intent", "planning"}
        if applicable:
            record = next((item for item in reversed(case["model_outputs"])
                           if "model_output" in item), {})
            raw = record.get("model_output")
            try:
                if stage == "intent":
                    parsed = parse_intent(raw, golden["question"])
                    observed_intent = parsed["type"]
                    observed_ids = sorted({mid for phrase in parsed["metrics"] for mid in names.get(phrase, ())})
                else:
                    parsed = parse_proposal(raw)
                    observed_intent = parsed["analysis_type"]
                    observed_ids = sorted(parsed["metric_ids"])
            except (ValueError, TypeError, RecursionError):
                pass
        case["proposal"] = {"intent": observed_intent, "metric_ids": observed_ids}
        case["scores"]["proposal_intent"] = (
            observed_intent == ("metric" if stage == "intent" else "summary") if applicable else None)
        case["scores"]["proposal_entity"] = observed_ids == sorted(golden["ids"]) if applicable else None
        if case["passed"] and applicable and not all(
                case["scores"][name] for name in ("proposal_intent", "proposal_entity")):
            case["failure"] = "proposal_corrected_or_misaligned"
        case["passed"] &= case["failure"] is None
    report["summary"] = summarize(report["cases"])
    report["summary"]["query_embedding_samples"] = sum(
        any(tool.get("query_embedding_called") for tool in case["answer"]["trace"]["tools"])
        for case in report["cases"]
    )
    return report


def evaluate_capture(case, answer, history, outputs, service, pg):
    scores, ods, actual = check(case, answer, history, outputs, service, pg)
    stage = case.get("stage")
    attempts = answer["trace"]["model_attempts"]
    scores["model_stage"] = (any(event["stage"] == stage and event["status"] == "received" for event in attempts)
                             if stage else not attempts)
    failed = [key for key, value in scores.items() if value is False]
    failure = None
    if failed:
        if any(item.get("error") == "external_scope_rejected" for item in outputs):
            failure = "external_scope_rejected"
        elif any("error" in item for item in outputs) and not any("model_output" in item for item in outputs):
            failure = "provider_error"
        elif failed == ["sql_provenance"]:
            failure = "sql_capability_trace_unlinked"
        else:
            failure = answer.get("code") or failed[0]
    return scores, ods, actual, failure


def rescore(report, pg):
    """Recheck captured results against ODS without resending any prompt."""
    service = MetricQueryService(None)
    for item in report["cases"]:
        scores, ods, actual, failure = evaluate_capture(item["golden"], item["answer"], item["history"],
                                                        item["model_outputs"], service, pg)
        item.update(scores=scores, ods=ods, actual=actual, failure=failure, passed=failure is None)
    return score_model_proposals(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument("--revision")
    options.add_argument("--rescore", type=Path)
    args = parser.parse_args()
    if args.rescore:
        with reference_connection() as pg:
            pg.execute("SET TRANSACTION READ ONLY")
            pg.execute("SET LOCAL statement_timeout = '15s'")
            report = rescore(json.loads(args.rescore.read_text()), pg)
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
        return 0
    manager, _ = local_manager()
    backend = manager.generate
    owner = os.environ.get("E2E_FIXTURE_OWNER") or token_hex(32)
    embedding = GeminiEmbedding.local()
    doc = parse_file(FIXTURES / "knowledge/refund_fixture.md", title="测试星店退款规则",
                     source_type="synthetic_fixture", source_uri="fixture:refund_fixture.md",
                     topic="refund_fixture", version="1")
    KnowledgeStore.local(admin=True).index(doc, embedding, owner=owner, external_approved=True)
    manager.agent.knowledge = KnowledgeService(Retriever(KnowledgeStore.local(), embedding),
                                              manager.agent.service.metrics, synthetic=True)
    cases, conversations = [], {}
    with reference_connection() as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        pg.execute("SET LOCAL statement_timeout = '15s'")
        for case in GOLDEN["cases"]:
            cid = (conversations[case["after"]] if "after" in case else
                   manager.create_conversation(owner)["conversation_id"])
            conversations[case["id"]] = cid
            model_outputs = []
            manager.generate = safe_generate(backend, model_outputs, doc, case["question"])
            started = monotonic()
            answer = manager.process_message(owner, cid, case["question"], uuid4(), today=TODAY)
            elapsed = round((monotonic() - started) * 1000)
            history = manager.get_query(owner, answer["query_id"]) if "query_id" in answer else None
            cases.append({"golden": case, "elapsed_ms": elapsed, "answer": answer,
                          "history": history, "model_outputs": model_outputs})
        report = rescore({"revision": args.revision, "mode": "real_gemini", "golden_version": GOLDEN["version"],
                          "cases": cases}, pg)
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
