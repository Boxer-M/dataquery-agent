"""Map existing evaluator observations to the unified public report."""
from hashlib import sha256
import json
import os
from time import monotonic
import unittest

from .dataset import ROOT, digest
from .scoring import evidence_ranking

ARCHIVE = ROOT / "reports/warehouse/agent_e2e_evaluation_v1.json"
ODS_TABLES = {"categories": "category_id", "customers": "customer_id", "products": "product_id",
              "orders": "order_id", "order_items": "order_item_id", "payments": "payment_id", "returns": "return_id"}


def snapshot(pg):
    """Exact content fingerprint, with only aggregate hashes crossing the DB boundary."""
    from psycopg import sql

    pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    pg.execute("SET LOCAL statement_timeout = '30s'")
    result = {}
    for table, key in ODS_TABLES.items():
        statement = sql.SQL("SELECT count(*) AS rows, md5(string_agg(md5(to_jsonb(t)::text), '' ORDER BY {})) "
                            "AS content_md5 FROM {} AS t").format(sql.Identifier(key), sql.Identifier("ods", table))
        result[table] = dict(pg.execute(statement).fetchone())
    return {"id": digest(result), "scope": "SQL Shed ODS content", "tables": result}


def trace_fields(trace, *, mode, elapsed):
    attempts = trace.get("model_attempts")
    actual = attempts if mode == "gemini" and attempts is not None else []
    row = {"latency_ms": elapsed, "tool_calls": len(trace["tools"]) if "tools" in trace else None,
           "model_calls": len(actual) if attempts is not None else None,
           "recorded_model_attempts": len(attempts) if attempts is not None else None,
           "prompt_versions": sorted({item["prompt_version"] for item in attempts or [] if item.get("prompt_version")}),
           "observed_models": sorted({item["model"] for item in actual if item.get("model")})}
    for field in ("prompt", "output", "total"):
        usage = [item["usage"][field] for item in actual if type(item.get("usage", {}).get(field)) is int]
        row[f"{field}_tokens"] = sum(usage) if usage else None
    return row


def fixed_or_postgres(mode):
    from dotenv import dotenv_values
    from dataquery_agent.capability_registry import CapabilityRegistry
    from dataquery_agent.metadata_service import MetadataService
    from dataquery_agent.metric_query_service import MetricQueryService
    from evaluate_unified_agent import CASES, evaluate, expected_calls, fixture_snapshot
    from evaluate_business_planning import reference_connection

    live = mode == "postgres"
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN") if live else None)
    metadata = MetadataService.local().snapshot() if live else fixture_snapshot()
    registry = CapabilityRegistry(metadata)

    def collect(reference):
        rows = []
        for case in CASES.values():
            if live and case["status"] not in {"ok", "detail"}:
                continue
            result = evaluate(case, mode, service, registry, reference, None)
            checks = result["scores"]
            expected = sorted({call["metric_id"] for call in expected_calls(case)})
            actual_ids = (result["match"] or {}).get("confirmed") or []
            rows.append({
                "case_id": "unified_agent_v1:" + case["id"],
                "intent_accuracy": checks["task_correct"],
                "entities": [expected, actual_ids] if expected and case["status"] == "ok" else None,
                "plan_validity": None,
                "tool_route_accuracy": checks["route_correct"],
                "sql_result_accuracy": checks["numeric_correct"] if live else None,
                "task_success": result["passed"],
                "error_category": (result["code"] or "fixture_assertion") if not result["passed"] else None,
                **trace_fields(result["trace"], mode=mode, elapsed=result["elapsed_ms"]),
            })
        return rows

    def planner_rows(reference):
        from evaluate_business_planning import CASES as PLANS, POSTGRES, evaluate_case

        for key in POSTGRES if live else PLANS:
            result = evaluate_case(PLANS[key], mode, service, reference, None)
            yield {"case_id": "business_planning_v1:" + key, "plan_validity": result["plan_correct"],
                   "tool_route_accuracy": result["tool_calls_correct"],
                   "sql_result_accuracy": result["numeric_correct"] if live else None,
                   "task_success": result["passed"], "tool_calls": result["tool_call_count"],
                   "model_calls": 0, "latency_ms": result["elapsed_ms"],
                   "prompt_versions": ["business-plan-v1"],
                   "error_category": "+".join(result["failures"]) or None}

    if live:
        with reference_connection() as pg:
            mark = snapshot(pg)
            rows = collect(pg) + list(planner_rows(pg)) + postgres_rag()
        with reference_connection() as pg:
            if snapshot(pg) != mark:
                raise ValueError("ODS changed during evaluation")
        return rows, mark, {"provider": "fixed", "embedding": "fixed-ngram-test-768-v1"}
    return collect(None) + list(planner_rows(None)), None, {"provider": "fixed"}


def postgres_rag():
    from secrets import token_hex
    from uuid import uuid4

    from dataquery_agent.conversation_manager import local_manager
    from dataquery_agent.rag.documents import parse_file
    from dataquery_agent.rag.store import KnowledgeStore
    from evaluate_agent_e2e import GOLDEN, TODAY
    from evaluate_business_knowledge import FixedEmbedding, fixed_model

    manager, _ = local_manager()
    manager.generate = fixed_model
    manager.agent.knowledge.retriever.embedding = FixedEmbedding()
    manager.agent.knowledge.synthetic = True
    writer = KnowledgeStore.local(admin=True)
    owner = token_hex(32)
    source = "fixture:refund_fixture.md"
    doc = parse_file(ROOT / "tests/fixtures/knowledge/refund_fixture.md", title="测试星店退款规则",
                     source_type="synthetic_fixture", source_uri=source, topic="refund_fixture", version="1")
    published = writer.index(doc, FixedEmbedding(), owner=owner, external_approved=True)
    rows = []
    try:
        for case in GOLDEN["cases"]:
            if case["check"] not in {"synthetic_policy", "insufficient_evidence"}:
                continue
            positive = case["check"] == "synthetic_policy"
            cid = manager.create_conversation(owner)["conversation_id"]
            started = monotonic()
            answer = manager.process_message(owner, cid, case["question"], uuid4(), today=TODAY)
            knowledge = answer["result"]
            retrieval = knowledge.get("retrieval_trace", {})
            candidates = retrieval.get("candidates", [])
            citations = knowledge.get("citations", [])
            ranked = [row | {"source_uri": source if row["document_id"] == published["document_id"] else None}
                      for row in candidates]
            verified = (bool(citations) and all(
                c.get("document_id") == published["document_id"] and c.get("document_version") == doc.version
                and any(r["chunk_id"] == c.get("chunk_id") for r in candidates)
                and any(p.location == c.get("source_location") and c.get("quote")
                        and c["quote"] in p.text for p in doc.passages) for c in citations
            )) if positive else None
            correct = (knowledge.get("evidence_status") == ("supported" if positive else "insufficient")
                       and (verified and any("七个自然日" in c["quote"] for c in citations) if positive else not citations))
            route = str(retrieval.get("strategy", "")).startswith("pgvector")
            rows.append({"case_id": "agent_e2e_v1:" + case["id"], "task_success": bool(correct and route),
                         "intent_accuracy": answer["task"] == case["intent"], "tool_route_accuracy": route,
                         "citation_validity": verified, "retrieval": evidence_ranking(
                             {"source": source, "heading": "可申请条件"}, ranked) if positive else None,
                         "error_category": None if correct and route else "knowledge_assertion",
                         **trace_fields(answer["trace"], mode="postgres", elapsed=round((monotonic() - started) * 1000))})
    finally:
        writer.delete(published["document_id"], owner=owner)
    return rows


class RecordedOds:
    """Serve only independently captured reference rows for the archived case."""
    def __init__(self, values):
        self.values = values

    def rows(self, request):
        from decimal import Decimal

        return {"all": {"value": Decimal(str(self.values[request["metric_id"]]))}}


def saved_replay(variant):
    from unittest.mock import patch
    from dataquery_agent.metric_query_service import MetricQueryService
    from evaluate_agent_e2e import GOLDEN, check

    capture = json.loads(ARCHIVE.read_text())[variant]
    expected = {row["id"]: row for row in GOLDEN["cases"]}
    rows = []
    for item in capture["cases"]:
        fixture = expected[item["golden"]["id"]]
        if item["golden"] != fixture:
            raise ValueError("capture fixture drift")
        answer = item["answer"]
        ods = RecordedOds(item["ods"])
        # The old helper still checks requests, rows and Decimal equality. Only its reference source changes.
        with patch("evaluate_agent_e2e.reference_rows", side_effect=lambda _, request: ods.rows(request)):
            result, _, observed = check(fixture, answer, item["history"], item["model_outputs"],
                                         MetricQueryService(None), ods)
        payload = answer["result"]
        knowledge = payload.get("knowledge_result", payload)
        retrieval = knowledge.get("retrieval_trace", {})
        relevant = None
        if fixture["check"] == "synthetic_policy":
            valid_source = "fixture:refund_fixture.md"
            # A rank denominator requires an independent relevance key. Do not infer it from returned answers.
            if retrieval.get("candidates") and all("source_uri" in r for r in retrieval["candidates"]):
                relevant = evidence_ranking({"source": valid_source, "heading": "可申请条件"},
                                            retrieval["candidates"])
        passed = all(value is not False for value in result.values())
        rows.append({
            "case_id": "agent_e2e_v1:" + fixture["id"],
            "intent_accuracy": result["intent"], "entities": [fixture["ids"], observed["metric_ids"]],
            "plan_validity": result["legal_plan"], "tool_route_accuracy": result["route"],
            "sql_result_accuracy": result["sql_numeric"], "citation_validity": result["catalog_citation"],
            "retrieval": relevant, "task_success": passed,
            "error_category": (item["failure"] or "replay_assertion") if not passed else None,
            **trace_fields(answer["trace"], mode="replay", elapsed=None),
        })
    return rows, {"id": "archived:" + sha256(ARCHIVE.read_bytes()).hexdigest(),
                  "scope": "independent archived ODS results; original database fingerprint unavailable"}, {
                      "archive_variant": variant, "captured_revision": capture["revision"],
                      "captured_model_attempts": capture["summary"]["model_calls"],
                      "captured_models": sorted({a["model"] for item in capture["cases"]
                                                 for a in item["answer"]["trace"]["model_attempts"] if a.get("model")}),
                      "latency_scope": "archived latency excluded from replay",
                  }


def deterministic():
    names = ("test_unified_agent.UnifiedAgentTests", "test_business_query_planner.BusinessPlannerTests",
             "test_metadata_capability.CapabilityUnitTests")
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)

    class Record(unittest.TestResult):
        def startTest(self, test):
            super().startTest(test)
            self.started = monotonic()
            self.failed = False

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err:
                self.failed = True

        def stopTest(self, test):
            super().stopTest(test)
            failures = self.failures + self.errors + self.unexpectedSuccesses
            failed = self.failed or any(test == item or isinstance(item, tuple) and test == item[0] for item in failures)
            skipped = any(test == item[0] for item in self.skipped)
            rows.append({"case_id": test.id(), "task_success": None if skipped else not failed,
                         "latency_ms": round(1000 * (monotonic() - self.started)), "model_calls": 0,
                         "error_category": "test_failure" if failed else None})

    rows = []
    suite.run(Record())
    return rows, None, {}


def live_environment():
    from dotenv import dotenv_values
    from dataquery_agent.agent_prompts import PROMPT_VERSIONS, OUTPUT_SCHEMAS
    from dataquery_agent.rag.embedding import GeminiEmbedding
    config = dotenv_values(ROOT / ".env")
    return {"configured_model": config.get("GEMINI_MODEL"), "prompt_versions": PROMPT_VERSIONS,
            "response_schema_hash": digest(OUTPUT_SCHEMAS), "response_format": "application/json",
            "embedding_model": GeminiEmbedding.model, "embedding_version": GeminiEmbedding.version}


def live_run(mode):
    from evaluate_business_planning import reference_connection

    with reference_connection() as pg:
        marker = snapshot(pg)
    if mode == "gemini":
        from evaluate_integrated_e2e import run
        report = run()
        rows = []
        for item in report["cases"]:
            check, case = item["scores"], item["golden"]
            trace = dict(item["trace"], model_attempts=item["model_attempts"])
            fields = trace_fields(trace, mode=mode, elapsed=item["elapsed_ms"])
            fields["model_calls"] -= sum(record.get("error") == "external_scope_rejected"
                                         for record in item["model_outputs"])
            citation = check.get("pdf_citation", check.get("catalog_citation"))
            suite = "agent_e2e_v1" if "intent" in case else "integrated_e2e_v1"
            rows.append({
                "case_id": f"{suite}:{case['id']}", "intent_accuracy": check.get("intent"),
                "plan_validity": check.get("legal_plan", check.get("dag")),
                "tool_route_accuracy": check.get("route", check.get("sql_provenance")),
                "sql_result_accuracy": check.get("sql_numeric"), "citation_validity": citation,
                "task_success": item["passed"], "error_category": item["failure"],
                **fields,
            })
        config = {"query_embedding_batches": report["summary"]["embedding_calls"],
                  "index_embedding_batches": len(report["setup_embedding_batches"])}
    else:
        from evaluate_integrated_browser import run
        report = run()
        rows = [{"case_id": "browser:workspace_assertions", "task_success": True}]
        rows += [{"case_id": "browser:chat:" + str(i), "task_success": None,
                  **trace_fields(request, mode="gemini", elapsed=None)}
                 for i, request in enumerate(report["api_requests"], 1)]
        config = {"fixture_deactivated": report["fixture_deactivated"], "count_unit": "one browser assertion bundle",
                  "embedding_batches": len(report["embedding_batches"]),
                  "browser_scenarios": {name: os.environ.get(name) == "1" for name in
                      ("WORKSPACE_DOCUMENT_UI_E2E", "WORKSPACE_SQL_DOCUMENT_E2E")}}
    with reference_connection() as pg:
        if snapshot(pg) != marker:
            raise ValueError("ODS changed during evaluation")
    return rows, marker, live_environment() | config
