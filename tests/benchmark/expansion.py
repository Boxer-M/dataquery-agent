"""Build and verify Golden Expansion v1 locally, without any model calls."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
import re

from dataquery_agent.metric_query_service import MetricQueryService, RequestError
from dataquery_agent.business_plan import PlanError, PlanValidator
from dataquery_agent.intent_semantic_resolver import CATEGORY
from dataquery_agent.intent_time import TIME
from dataquery_agent.metadata_service import fingerprint
from dataquery_agent.rag.documents import PARSER_VERSION, catalog_documents, split_sections

from .adapters import snapshot
from .dataset import DATASET, ROOT, VERSION, digest, migrate, normalize, validate
from .scoring import scores

DIRECTORY = DATASET.parent
PUBLIC = DIRECTORY / "golden_expansion_v1.json"
AUTHORED = DIRECTORY / "expansion_cases.tsv"
MANIFEST = DIRECTORY / "golden_expansion_holdout_manifest_v1.json"
REPORT = ROOT / "reports/benchmark/golden_expansion_v1.json"
RELEASE = "benchmark-golden-expansion-v1"
SPLITS = {"V": "validation", "H": "holdout", "P": "development"}
DOCS = {"table": "TABLE_DESIGN.md", "asset": "QUERY_ASSET_GOVERNANCE.md"}
ODS = ROOT / "sql/warehouse/dws_metrics/ods.sql"
KEYS = ("case_id", "semantic_group_id", "template_id", "normalized_text_id")


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def group_id(value):
    return digest(value)[:20]


def question_key(question):
    return normalize(re.sub(r"20\d{2}(?:年|[-/])?|\d{1,2}(?:月|日|季度)|第[一二三四]季度", "", question))


def similarity_review(cases):
    """Flag date variants, exact matches and close surface rewrites across splits."""
    flagged = []
    keys = [(row, question_key(row["question"])) for row in cases]
    for i, (left, lhs) in enumerate(keys):
        if left["review_status"] == "needs_review":
            continue
        for right, rhs in keys[:i]:
            if left["split"] == right["split"] or right["review_status"] == "needs_review":
                continue
            if lhs == rhs or min(len(lhs), len(rhs)) >= 10 and SequenceMatcher(None, lhs, rhs).ratio() >= .85:
                flagged.append([left["case_id"], right["case_id"]])
    return flagged


def metric_request(mid, axis, start, end, filter_spec=""):
    name, _, value = filter_spec.partition("=")
    return {"metric_id": mid, "start_date": start, "end_date": end, "group_by": [axis] if axis else [],
            "filters": {name: int(value) if name == "category" else value} if name else {}}


def validate_requests(service, question, requests, case_id):
    """Check that authored requests are expressible via the current deterministic gate."""
    validator = PlanValidator(service)
    windows = [match.group() for match in TIME.finditer(question)]
    groups = [match.group() for match in validator.resolver.group_pattern.finditer(question)]
    filters = validator.resolver.channel_pattern.findall(question) + [
        match.group() for match in CATEGORY.finditer(question)
    ]
    proposal = {"analysis_type": "summary", "metric_ids": [r["metric_id"] for r in requests],
                "time_windows": windows, "group_by": groups, "filters": filters, "limit": None,
                "comparison_type": None, "sort_rule": None, "conditions": [], "unresolved_business_rules": []}
    try:
        result = validator.validate(question, proposal, date(2025, 12, 31))
    except PlanError as error:
        raise ValueError(f"{case_id}: {error.response['code']}") from error
    if [call["request"] for call in result["required_tool_calls"]] != requests:
        raise ValueError(f"{case_id}: validated requests differ from authored parameters")


def sql_label(pg, service, requests, mark):
    from evaluate_business_planning import reference_rows

    calls = []
    for request in requests:
        metric, *_ = service.validate(request)
        rows = list(reference_rows(pg, request).values())
        rows.sort(key=lambda row: (row["bucket"] is None, row["bucket"] or ""))
        # Canonical JSON uses exact Decimal strings, retaining ratio components.
        expected = json.loads(json.dumps(rows, default=str))
        actual = service.query(request)
        if actual["status"] != "ok" or compare_rows(expected, actual["results"]) is not True:
            raise ValueError(f"ODS/service disagreement for {request['metric_id']}")
        calls.append({"parameters": request, "time_attribution": metric["time_attribution"],
                      "expected_result": expected})
    return {"method": str(ODS.relative_to(ROOT)), "query_sha256": sha256(ODS.read_bytes()).hexdigest(),
            "snapshot": mark["id"], "calls": calls}


def compare_rows(expected, observed):
    """Compare aligned rows at full decimal precision, including ratio components."""
    if observed is None:
        return None
    if not isinstance(observed, list):
        return False

    def exact(row):
        return {key: value if key == "bucket" or value is None else Decimal(str(value))
                for key, value in row.items()}

    try:
        return [exact(row) for row in expected] == [exact(row) for row in observed]
    except (ArithmeticError, AttributeError, TypeError, ValueError):
        return False


def source_evidence(key, quote):
    source = f"docs/warehouse/{DOCS[key]}"
    raw = (ROOT / source).read_bytes()
    content_hash = sha256(raw).hexdigest()
    version = f"{content_hash[:12]}-benchmark-full-v1"
    matches = []
    for index, passage in enumerate(split_sections(raw.decode("utf-8"))):
        if quote in passage.text:
            matches.append({"chunk_index": index, "location": passage.location, "heading_path": list(passage.heading_path),
                            "passage_hash": fingerprint(asdict(passage))})
    if len(matches) != 1:
        raise ValueError(f"Expected one source passage for {key}: {len(matches)}")
    chunk = matches[0]
    return {"source": source, "version": version, "content_hash": content_hash, "parser_version": PARSER_VERSION,
            "quote": quote, "chunk": chunk,
            "relevant_chunks": [digest([source, version, chunk["location"], chunk["passage_hash"]])],
            "id_kind": "source locator independent of owner and embedding index",
            "corpus": "offline_full_document_requires_local_provisioning"}


def catalog_label(service, mid):
    metric = service.metrics.get(mid)
    if metric and mid in service.registry:
        doc = next(doc for doc in catalog_documents(service) if doc.source_uri.endswith(f"metric_id={mid}]"))
        return {"metric_ids": [mid], "tool_path": "catalog_exact", "status": "ok",
                "evidence": {"source": doc.source_uri, "version": doc.version, "content_hash": doc.content_hash,
                             "definition": metric["definition"], "formula": metric["formula"],
                             "quote": metric["definition"]}}
    if metric is None:
        return {"metric_ids": [], "status": "ok", "code": "formal_definition_not_found",
                "tool_path": "catalog_exact", "entity": {"unmatched_id": mid, "evidence_status": "insufficient"}}
    try:
        service.validate(metric_request(mid, "", "2025-01-01", "2025-02-01"))
    except RequestError as error:
        return {"metric_ids": [mid], "status": "unsupported", "code": error.code,
                "entity": {"catalog_status": metric["definition_status"]}}
    raise ValueError("Unavailable case unexpectedly allowed")


def read_authored(path):
    return [line.split("|", 4) for line in path.read_text().splitlines() if line and not line.startswith("#")]


def build_case(record, pg, service, mark):
    split, category, family, label, question = record
    case_id = f"exp1:{category}:{family}" if category != "rag" else f"exp1:rag:{group_id(question)}"
    if split == "H":
        case_id = f"exp1:h:{group_id(case_id)}"
    context, review = {}, {"state": "independent_source_checked", "human_signoff": None}
    golden = dict.fromkeys(("intent", "entity", "metric_ids", "metric_request", "plan", "tool_path",
                            "sql", "evidence", "status", "code"))
    if split == "P":
        if category == "conversation":
            before, question = question.removeprefix("前问：").split("后问：", 1)
            context["prior_user_text"] = before
        review = {"state": "pending", "reason": label, "human_signoff": None}
    elif category == "rag":
        source, quote = label.split(";", 1)
        golden.update(intent="knowledge", tool_path="rag", status="ok", evidence=source_evidence(source, quote))
    elif category == "intent":
        golden.update(catalog_label(service, label))
        golden["intent"] = "catalog_definition" if golden["tool_path"] or not golden["metric_ids"] else "metric"
    else:
        if category == "planning":
            ids, axis, start, end = label.split(";")
            requests = [metric_request(mid, axis, start, end) for mid in ids.split(",")]
            golden["plan"] = {"analysis_type": "summary", "operation": "align_by_bucket", "requests": requests,
                              "no_cross_fact_join": True, "retain_per_metric_event_time": True}
        else:
            requests = [metric_request(*label.split(";"))]
            golden["metric_request"] = requests[0]
        validate_requests(service, question, requests, case_id)
        golden.update(intent="business_plan" if category == "planning" else "metric", status="ok",
                      metric_ids=[r["metric_id"] for r in requests], tool_path="metric_query",
                      sql=sql_label(pg, service, requests, mark))
    return {"case_id": case_id, "task_category": category, "question": question, "context": context,
            "source": {"file": "sealed-authored.tsv" if split == "H" else str(AUTHORED.relative_to(ROOT)),
                       "pointer": case_id, "original_id": case_id},
            "split": SPLITS[split], "normalized_text_id": group_id(normalize(question)),
            "semantic_group_id": group_id(["expansion-semantic", family]),
            "template_id": group_id(["expansion-template", family]),
            "duplicate_text": False, "review_status": "needs_review" if split == "P" else "reviewed",
            "review": review, "golden": golden,
            "scoring": {"source": "independent_evidence", "legacy_rule": None,
                        "citation_support": "manual" if golden["evidence"] else None}}


def legacy_review(pg, old):
    from evaluate_controlled_detail import FIXTURE, reference
    from evaluate_dynamic_sql import FIXTURE as DYNAMIC, TODAY, ground_truth, recognize
    from psycopg.rows import tuple_row

    archive = ROOT / "reports/warehouse"
    detail_runs = read(archive / "controlled_detail_v1.json")["live_detail_runs"]
    dynamic_runs = read(archive / "dynamic_sql_v1.json")["evaluations"]
    saved = {f"controlled_detail_v1:{row['id']}": (row["rows"], row["truncated"])
             for row in detail_runs[-1]["cases"] if row["passed"]}
    saved.update({f"dynamic_sql_v1:{row['id']}": (row["expected_rows"], row["expected_truncated"])
                  for row in dynamic_runs["live"]["cases"] if row["ods_equal"]})
    evidence = {}
    # The legacy Detail reference expects tuple rows; snapshot / dynamic helpers use mappings.
    with pg.cursor(row_factory=tuple_row) as cursor:
        for case in FIXTURE["live_cases"]:
            rows, truncated = reference(cursor, case)
            evidence["controlled_detail_v1:" + case["id"]] = (rows, truncated, "evaluate_controlled_detail.py")
    for case in DYNAMIC["live_cases"]:
        rows, truncated = ground_truth(pg, recognize(case["question"], TODAY))
        evidence["dynamic_sql_v1:" + case["id"]] = (rows, truncated, "evaluate_dynamic_sql.py")
    result = []
    for row in old["cases"]:
        if row["review_status"] != "needs_review":
            continue
        case_id = row["case_id"]
        is_lifecycle = case_id.startswith("query_assets")
        lookup = "dynamic_sql_v1:top" if is_lifecycle else case_id
        rows, truncated, method = evidence[lookup]
        if saved[lookup] != (len(rows), truncated):
            raise ValueError(f"legacy numeric review drift: {lookup}")
        result.append({"case_id": case_id, "review": "numeric_only_lifecycle_pending" if is_lifecycle else "ods_recomputed",
                       "reference": "tests/" + method, "row_count": len(rows), "truncated": truncated,
                       "rows_sha256": digest(rows), "archived_row_count_and_truncation_match": True})
    return result


def envelope(cases):
    return {"version": VERSION, "dataset_version": RELEASE, "cases": cases}


def seal(private):
    return {"dataset_version": RELEASE, "sha256": digest(private),
            "cases": [{key: row[key] for key in KEYS} | {"task_category": row["task_category"]}
                      for row in private["cases"]]}


def verify(public, manifest, old, private=None):
    if public["dataset_version"] != RELEASE or manifest["dataset_version"] != RELEASE:
        raise ValueError("expansion release mismatch")
    if any(row["split"] == "holdout" for row in public["cases"]):
        raise ValueError("holdout leaked into public dataset")
    if len({row["case_id"] for row in manifest["cases"]}) != len(manifest["cases"]):
        raise ValueError("duplicate sealed case")
    public_ids = {key: {row[key] for row in old["cases"] + public["cases"]} for key in KEYS}
    if any(row[key] in public_ids[key] for row in manifest["cases"] for key in KEYS):
        raise ValueError("sealed holdout overlaps public identifiers")
    rows = old["cases"] + public["cases"]
    if private is not None:
        if manifest != seal(private) or any(row["split"] != "holdout" for row in private["cases"]):
            raise ValueError("holdout seal mismatch")
        rows += private["cases"]
    validate(envelope(rows))
    if similarity_review(rows):
        raise ValueError("cross-split near duplicate detected")
    for row in rows[len(old["cases"]):]:
        if row["review_status"] == "needs_review" and (row["split"] != "development" or
                                                        any(row["golden"].values())):
            raise ValueError("pending cases must not carry scoreable labels")
    return len(rows)


def build(public_source, private_source):
    from dotenv import dotenv_values
    from evaluate_business_planning import reference_connection

    old = read(DATASET)
    if old != migrate()[0]:
        raise ValueError("legacy baseline drift")
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
    with reference_connection() as pg:
        mark = snapshot(pg)
        review = legacy_review(pg, old)
        public = envelope([build_case(row, pg, service, mark) for row in read_authored(public_source)])
        private = envelope([build_case(row, pg, service, mark) for row in read_authored(private_source)])
    with reference_connection() as pg:
        if snapshot(pg) != mark:
            raise ValueError("ODS snapshot changed during verification")
    manifest = seal(private)
    verify(public, manifest, old, private)
    rows = public["cases"] + private["cases"]
    checked = [row for row in rows if row["review_status"] == "reviewed"]
    report = {"dataset_version": RELEASE, "historical_development": len(old["cases"]),
              "legacy_review": review, "ods_snapshot": mark, "model_calls": 0,
              "split_counts": dict(Counter(row["split"] for row in rows)),
              "category_split_counts": {category: dict(Counter(row["split"] for row in rows
                                                              if row["task_category"] == category))
                                        for category in sorted({row["task_category"] for row in rows})},
              "cross_split_conflicts": 0, "reviewed_count": len(checked), "pending_count": len(rows) - len(checked),
              "ods_case_count": sum(bool(row["golden"]["sql"]) for row in checked),
              "ods_call_count": sum(len(row["golden"]["sql"]["calls"]) for row in checked if row["golden"]["sql"]),
              "source_evidence_case_count": sum(bool(row["golden"]["evidence"]) for row in checked),
              "scoring_eligible": {
                  "intent_and_tool_route": sum(bool(row["golden"]["tool_path"]) for row in checked),
                  "entity": sum(row["golden"]["metric_ids"] is not None for row in checked),
                  "business_plan": sum(bool(row["golden"]["plan"]) for row in checked),
                  "sql": sum(bool(row["golden"]["sql"]) for row in checked),
                  "retrieval_offline_corpus": sum(row["task_category"] == "rag" for row in checked),
                  "citation_source_location": sum(bool(row["golden"]["evidence"]) for row in checked),
                  "citation_answer_support_requires_human": sum(bool(row["golden"]["evidence"]) for row in checked),
              },
              "performance_metrics": scores([{} for _ in checked])}
    return public, private, manifest, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reproduce", action="store_true", help="Recompute approved ODS and source labels locally")
    parser.add_argument("--holdout-dir", type=Path)
    parser.add_argument("--write-release", action="store_true", help="Maintainer only, initially seal the release")
    args = parser.parse_args()
    private_dir = args.holdout_dir
    if args.write_release or args.reproduce:
        if private_dir is None:
            parser.error("reproduction requires the sealed local directory")
        public, private, manifest, report = build(AUTHORED, private_dir / "holdout.tsv")
        if args.write_release:
            if any(path.exists() for path in (PUBLIC, MANIFEST, REPORT, private_dir / "holdout.json")):
                parser.error("existing release is immutable; generate a new dataset version")
            private_dir.chmod(0o700)
            write(private_dir / "holdout.json", private)
            (private_dir / "holdout.json").chmod(0o600)
            write(PUBLIC, public)
            write(MANIFEST, manifest)
            write(REPORT, report)
        elif (public, private, manifest, report) != (
            read(PUBLIC), read(private_dir / "holdout.json"), read(MANIFEST), read(REPORT)
        ):
            raise ValueError("reproduced evidence differs from the release")
    else:
        count = verify(read(PUBLIC), read(MANIFEST), read(DATASET),
                       read(private_dir / "holdout.json") if private_dir else None)
        scope = "including local holdout" if private_dir else "without opening holdout"
        print(f"Verified {count} cases ({scope}); seal lists {len(read(MANIFEST)['cases'])} holdout cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
