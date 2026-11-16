"""Migrate human-authored fixtures without using historical model responses."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
DATASET = ROOT / "tests/benchmark/golden_v1.json"
VERSION = "benchmark-golden-v1"
REFERENCE = "tests/evaluate_business_planning.py#reference_rows"
TEMPLATE_EQUIVALENTS = (
    ("2025年1月的订单量", "去年有多少下单笔数", "从2025-01-01到2025-01-31的订单量"),
)
APPROVED_EQUIVALENTS = (
    ("2025年1月的订单量", "2025年1月订单量", "2025年1月下单笔数", "2025年1月下单总共几笔"),
    ("item_return_rate", "商品明细退货率怎么定义？"),
    ("订单量多少", "订单量是多少"),
    ("2025年1月销售额", "2025年1月的销售额"),
)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def normalize(question):
    return re.sub(r"[^\w]+|_", "", unicodedata.normalize("NFKC", question).casefold())


def walk(value, pointer=""):
    if isinstance(value, dict):
        if isinstance(value.get("question"), str):
            yield pointer, value
        for key, child in value.items():
            yield from walk(child, f"{pointer}/{key.replace('~', '~0').replace('/', '~1')}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{pointer}/{index}")


def case_category(suite, row):
    if suite == "agent_e2e_v1":
        return {"knowledge": "rag", "hybrid": "hybrid", "business": "planning"}.get(row["intent"], "intent")
    if suite == "integrated_e2e_v1":
        return {"combined": "hybrid", "pdf": "pdf", "absent": "pdf", "catalog": "rag"}.get(row["check"], "intent")
    return {
        "intent_business_v1": "intent", "unified_agent_v1": "intent", "business_planning_v1": "planning",
        "controlled_detail_v1": "sql", "dynamic_sql_v1": "sql", "query_assets_v1": "sql",
        "rag_v1": "rag", "conversation_v1": "conversation",
    }[suite]


def migrate():
    rows = []
    exact_counts = Counter()
    sources = defaultdict(set)
    equivalences = {normalize(text): normalize(group[0]) for group in APPROVED_EQUIVALENTS for text in group}
    template_groups = {normalize(text): normalize(group[0]) for group in TEMPLATE_EQUIVALENTS for text in group}
    for path in sorted(FIXTURES.rglob("*.json")):
        suite = path.stem
        content = json.loads(path.read_text())
        date = content.get("today") if isinstance(content, dict) else None
        for pointer, row in walk(content):
            text_key = normalize(row["question"])
            exact_counts[text_key] += 1
            sources[text_key].add(suite)
            semantic_key = equivalences.get(text_key, text_key)
            template = re.sub(r"\d{4}年|\d{1,2}[月日]", "{date}", template_groups.get(semantic_key, semantic_key))
            expected = row.get("expected")
            expected = expected if isinstance(expected, dict) else {}
            request = row.get("request") or {}
            intent = row.get("task") or (row["intent"] if isinstance(row.get("intent"), str) else None)
            ids = row.get("ids", expected.get("metrics"))
            if ids is None and (metric := row.get("metric") or request.get("metric_id")):
                ids = [metric]
            check = row.get("check") or ""
            if suite == "agent_e2e_v1":
                tool_path = row["path"]
            elif suite == "unified_agent_v1":
                tool_path = row.get("source") or ("metric_query_service" if row.get("status") == "ok" else None)
            elif suite == "business_planning_v1" and "calls" in expected:
                tool_path = "metric_query_service" if expected["calls"] else "no_tool"
            else:
                tool_path = None
            if suite == "integrated_e2e_v1" and check in {"scalar", "month", "channel", "combined"} and not ids:
                ids = ["order_count"]
            ods = {"method": REFERENCE, "snapshot": "sqlshed-public-v1"} if (
                "ods" in check or suite == "integrated_e2e_v1" and check in {"scalar", "month", "channel", "combined"}
            ) else None
            evidence = None
            if check in {"pdf", "combined"}:
                evidence = {"source": "output/pdf/document-extraction-sample.pdf", "page": row["page"], "quote": row["quote"]}
            elif check == "synthetic_policy" or row.get("quote"):
                evidence = {"source": "fixture:refund_fixture.md", "heading": "可申请条件",
                            "quote": row.get("quote", "七个自然日")}
            elif "catalog" in check or row.get("topic") or row.get("topics"):
                evidence = {"source": "metrics/warehouse_catalog_v1.json", "metric_ids": ids or row.get("topics") or [row.get("topic")]}
            rows.append({
                "case_id": f"{suite}:{row.get('id') or pointer}", "source": {
                    "file": str(path.relative_to(ROOT)), "pointer": pointer, "original_id": row.get("id"),
                },
                "question": row["question"], "task_category": case_category(suite, row),
                "semantic_group_id": digest(semantic_key)[:20], "template_id": digest(template)[:20],
                "normalized_text_id": digest(text_key)[:20],
                "split": "development", "review_status": "legacy_reviewed" if suite in {
                    "agent_e2e_v1", "integrated_e2e_v1", "unified_agent_v1", "intent_business_v1",
                    "business_planning_v1", "rag_v1"} else "needs_review",
                "context": {key: row[key] for key in ("after", "foreign", "withdraw", "tool", "tool_error", "mutation") if key in row}
                           | ({"today": date} if date else {}),
                "golden": {
                    "intent": intent, "metric_request": request or None, "entity": row.get("intent") if isinstance(row.get("intent"), dict) else None,
                    "metric_ids": ids, "plan": expected.get("type") and {key: expected[key] for key in
                        ("type", "metrics", "windows", "groups", "calls", "operation", "limit") if key in expected} or None,
                    "tool_path": tool_path, "sql": ods, "evidence": evidence,
                    "status": row.get("status", expected.get("status")), "code": row.get("code", expected.get("code")),
                },
                "scoring": {"source": "fixture", "citation_support": "manual", "legacy_rule": check or row.get("truth")},
            })
    for row in rows:
        row["duplicate_text"] = exact_counts[normalize(row["question"])] > 1
    result = {"version": VERSION, "cases": rows}
    validate(result)
    return result, {
        "question_records": len(rows), "unique_normalized_texts": len(exact_counts),
        "duplicate_text_groups": sum(count > 1 for count in exact_counts.values()),
        "cross_file_duplicate_groups": sum(len(group) > 1 for group in sources.values()),
        "cross_file_groups": [
            {"normalized_text_id": digest(key)[:20],
             "case_ids": [row["case_id"] for row in rows if row["normalized_text_id"] == digest(key)[:20]]}
            for key, suites in sorted(sources.items()) if len(suites) > 1
        ],
        "semantic_groups": len({row["semantic_group_id"] for row in rows}),
        "templates": len({row["template_id"] for row in rows}),
        "review_status": dict(Counter(row["review_status"] for row in rows)),
        "split": dict(Counter(row["split"] for row in rows)),
    }


def validate(dataset):
    if dataset.get("version") != VERSION or not isinstance(dataset.get("cases"), list):
        raise ValueError("unsupported dataset version")
    ids = set()
    splits = defaultdict(set)
    for row in dataset["cases"]:
        for key in ("case_id", "semantic_group_id", "template_id", "task_category", "question"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"invalid {key}")
        if row["normalized_text_id"] != digest(normalize(row["question"]))[:20]:
            raise ValueError("invalid normalized_text_id")
        if row["case_id"] in ids:
            raise ValueError("duplicate case_id")
        ids.add(row["case_id"])
        if row["split"] not in {"development", "validation", "holdout"}:
            raise ValueError("invalid split")
        if row["review_status"] not in {"legacy_reviewed", "needs_review", "reviewed"}:
            raise ValueError("invalid review_status")
        if row["split"] == "holdout" and row["review_status"] != "reviewed":
            raise ValueError("holdout must be independently reviewed")
        for key in ("semantic_group_id", "template_id", "normalized_text_id"):
            splits[key, row[key]].add(row["split"])
    if any(len(value) > 1 for value in splits.values()):
        raise ValueError("related cases cross splits")
