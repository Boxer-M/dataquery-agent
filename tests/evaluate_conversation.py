"""Persist the three business demos and compare actual results to independent ODS."""
import argparse
import json
from datetime import date
from decimal import Decimal
from secrets import token_hex
from uuid import uuid4

from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.dynamic_requirements import recognize
from dataquery_agent.metrics import ROOT
from evaluate_business_planning import reference_connection, reference_rows
from evaluate_dynamic_sql import fixed_candidate, ground_truth

FIXTURE = json.loads((ROOT / "tests/fixtures/conversation_v1.json").read_text())
TODAY = date(2025, 3, 10)


def fixed_model(prompt):
    if prompt.startswith("[dynamic-sql-"):
        return fixed_candidate(prompt)
    return json.dumps({"metrics": ["成功支付金额"], "time": "2025年1月", "groups": [],
                       "filters": ["web"], "type": "metric", "missing": []}, ensure_ascii=False)


def reconcile(case, answer, history, pg):
    if case["id"] == "dynamic":
        requirement = recognize(history["original_question"], TODAY)
        expected, truncated = ground_truth(pg, requirement)
        return (answer["status"] == "ok" and answer["result"]["results"] == expected
                and history["result_summary"]["truncated"] == truncated
                and history["sql"] == answer["result"]["sql"])
    if answer["status"] == "clarify":
        return case["id"] == "clarification" and bool(answer["choices"]) and not answer["trace"]["tools"]
    if answer["status"] != "ok":
        return False
    request = history["resolved_request"]
    if case["id"] == "clarification" and request["metric_id"] != "item_net_sales_amount":
        return False
    if case["id"] == "followup" and (request["metric_id"] != "captured_payment_amount"
                                      or request.get("filters") != {"channel": "web"}):
        return False
    expected = reference_rows(pg, {"group_by": [], "filters": {}} | request)
    rows = answer["result"]["query_result"]["results"]
    return len(rows) == 1 and Decimal(rows[0]["value"]) == expected["all"]["value"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "live"), default="fixed")
    mode = parser.parse_args().mode
    manager, _ = local_manager()
    backend = manager.generate if mode == "live" else fixed_model
    owner = token_hex(32)
    report = []
    with reference_connection() as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        pg.execute("SET LOCAL statement_timeout = '15s'")
        for case in FIXTURE["scenarios"]:
            cid = manager.create_conversation(owner)["conversation_id"]
            turns = []
            for index, message in enumerate(case["messages"]):
                before = manager.get_conversation(owner, cid)
                raw_outputs = []

                def generate(prompt):
                    item = backend(prompt)
                    raw_outputs.append(item)
                    return item

                manager.generate = generate
                message_id = uuid4()
                answer = manager.process_message(owner, cid, message, message_id, today=TODAY)
                after = manager.get_conversation(owner, cid)
                history = manager.get_query(owner, answer["query_id"]) if answer.get("query_id") else None
                passed = bool(history and reconcile(case, answer, history, pg)
                              and history["trace_id"] == answer["trace"]["trace_id"]
                              and after["state_version"] == before["state_version"] + 1)
                if passed and case["id"] == "followup" and index == 1:
                    passed = history["resolved_request"]["start_date"] == "2025-02-01"
                if passed and case["id"] == "clarification" and index == 0:
                    passed = bool(after["pending_clarification"])
                if passed and case["id"] == "clarification" and index == 1:
                    passed = after["pending_clarification"] is None and answer["context_operation"] == "clarification"
                # The returned synthetic-data result is verified in full; persist a bounded report preview.
                if case["id"] == "dynamic" and answer.get("result", {}).get("results"):
                    answer["result"] = dict(answer["result"], results_preview=answer["result"]["results"][:3],
                                            results=None)
                turns.append({"message": message, "state_before": before, "model_outputs": raw_outputs,
                              "answer": answer, "state_after": after, "history": history, "passed": passed})
            report.append({"id": case["id"], "passed": all(turn["passed"] for turn in turns), "turns": turns})
    print(json.dumps({"mode": mode, "passed": sum(case["passed"] for case in report),
                      "total": len(report), "cases": report}, default=str, ensure_ascii=False, indent=2))
    return 0 if all(case["passed"] for case in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
