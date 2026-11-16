"""Aggregate observable checks while keeping missing labels separate."""
from collections import Counter
from math import ceil

BINARY = ("intent_accuracy", "plan_validity", "tool_route_accuracy", "sql_result_accuracy",
          "citation_validity", "task_success")


def ratio(numerator, denominator, na):
    return {"numerator": numerator, "denominator": denominator, "not_applicable": na,
            "value": numerator / denominator if denominator else None}


def scores(rows, *, k=6):
    summary = {}
    for name in BINARY:
        values = [row.get(name) for row in rows]
        applicable = [value for value in values if value is not None]
        summary[name] = ratio(sum(applicable), len(applicable), len(values) - len(applicable))
    entities = [row["entities"] for row in rows if row.get("entities") is not None]
    hits = predicted = truth = 0
    for expected, actual in entities:
        expected, actual = set(expected), set(actual)
        hits += len(expected & actual)
        truth += len(expected)
        predicted += len(actual)
    na = len(rows) - len(entities)
    summary.update(entity_precision=ratio(hits, predicted, na), entity_recall=ratio(hits, truth, na),
                   entity_f1=ratio(2 * hits, truth + predicted, na))
    hits, rr, relevant, usable = 0, 0, 0, 0
    for row in rows:
        if row.get("retrieval") is None:
            continue
        expected, ranked = row["retrieval"]
        expected = set(expected)
        if not expected:
            continue
        usable += 1
        relevant += len(expected)
        top = list(dict.fromkeys(ranked))[:k]
        hits += len(expected.intersection(top))
        rr += next((1 / index for index, item in enumerate(top, 1) if item in expected), 0)
    summary[f"retrieval_recall_at_{k}"] = ratio(hits, relevant, len(rows) - usable)
    summary[f"retrieval_mrr_at_{k}"] = ratio(rr, usable, len(rows) - usable)
    summary["citation_semantic_support"] = ratio(0, 0, len(rows)) | {"evaluation": "manual"}
    return summary


def telemetry(rows):
    def measure(name):
        values = [row[name] for row in rows if row.get(name) is not None]
        return {"value": sum(values) if values else None, "numerator": sum(values) if values else 0,
                "denominator": len(values), "not_applicable": len(rows) - len(values)}

    times = sorted(row["latency_ms"] for row in rows if row.get("latency_ms") is not None)
    return {
        **{name: measure(name) for name in ("model_calls", "tool_calls", "prompt_tokens", "output_tokens", "total_tokens")},
        "latency_ms": {**{name: times[ceil(q * len(times)) - 1] if times else None
                          for name, q in (("p50", .5), ("p95", .95))},
                       "denominator": len(times), "not_applicable": len(rows) - len(times)},
        "error_categories": dict(Counter(row["error_category"] for row in rows if row.get("error_category"))),
    }


def evidence_ranking(expected, candidates):
    if not expected or "heading" not in expected or candidates is None:
        return None
    target = expected["source"] + "#" + expected["heading"]
    ranked = [target if expected["source"] == row.get("source_uri")
              and expected["heading"] in row.get("heading_path", []) else row["chunk_id"] for row in candidates]
    return [[target], ranked]
