"""Capture planning proposals while running the existing authorized live benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from dataquery_agent.agent_prompts import PROMPT_VERSIONS, schema_for_prompt
from dataquery_agent.business_plan import PROPOSAL_FIELDS
from dataquery_agent.conversation_context import redact
from dataquery_agent.metric_query_service import MetricQueryService

from . import live_baseline

TARGETS = {"exp1:planning:" + tag for tag in ("gross-discount", "items-products", "account-pair", "payment-pair")}


def ordered_inventory(**kwargs):
    rows, excluded, approved, reviewed = ORIGINAL_INVENTORY(**kwargs)
    return sorted(rows, key=lambda item: item[0]["case_id"] not in TARGETS), excluded, approved, reviewed


ORIGINAL_INVENTORY = live_baseline.inventory
ORIGINAL_EXECUTE = live_baseline.execute_case


def run(destination):
    service = MetricQueryService(None)
    traces = {}
    proposal_path = destination.with_suffix(".proposals.json")

    def capture(case, spec, family, manager, owner, conversation, pg, budget, generate, blocked, calls, candidates):
        attempts = []

        def traced(prompt):
            version = PROMPT_VERSIONS["planning"]
            if not prompt.startswith(f"[{version}]"):
                return generate(prompt)
            schema = schema_for_prompt(prompt)
            event = {"prompt_version": version,
                     "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                     "catalog_metric_count": sum(f"{key}=" in prompt for key in service.metrics),
                     "context_lines": [line for line in prompt.splitlines() if line.startswith("已匹配正式指标")],
                     "schema_fields": sorted((schema or {}).get("properties", {}))}
            attempts.append(event)
            try:
                response = generate(prompt)
            except Exception as error:
                event["error_type"] = type(error).__name__
                event["http_status"] = getattr(error, "code", None)
                raise
            event["model"] = response.get("model")
            try:
                proposal = json.loads(response.get("text") or "")
                if isinstance(proposal, dict):
                    allowed = PROPOSAL_FIELDS | {"pdf_question"}
                    event["proposal"] = json.loads(redact(json.dumps(
                        {key: value for key, value in proposal.items() if key in allowed}, ensure_ascii=False)))
                    event["extra_fields_count"] = len(set(proposal) - allowed)
            except (ValueError, TypeError):
                event["structured_capture"] = "invalid_json_or_redacted"
            return response

        row = ORIGINAL_EXECUTE(case, spec, family, manager, owner, conversation, pg, budget,
                               traced, blocked, calls, candidates)
        if attempts:
            traces[row["case_id"]] = {"question": case["question"], "expected": case["golden"].get("plan"),
                                      "attempts": attempts, "status": row["status"], "code": row["code"],
                                      "plan_validity": row.get("plan_validity")}
            proposal_path.write_text(json.dumps(traces, ensure_ascii=False, indent=2) + "\n")
        return row

    with patch.object(live_baseline, "execute_case", capture), patch.object(live_baseline, "inventory", ordered_inventory):
        live_baseline.run(destination, authorized_technical=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--allow-live-model", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_model or args.out.exists() or args.out.with_suffix(".proposals.json").exists():
        parser.error("provide --allow-live-model and a fresh output path")
    run(args.out)


if __name__ == "__main__":
    main()
