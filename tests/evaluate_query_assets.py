"""Publish the channel ranking demo and preserve real history plus isolated drift evidence."""
import argparse
import json
from copy import deepcopy
from secrets import token_hex
from uuid import uuid4

from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.dynamic_requirements import recognize
from dataquery_agent.metrics import ROOT
from dataquery_agent.query_asset_contract import OPERATION
from dataquery_agent.query_asset_governance import QueryAssetGovernance, QueryAssetRuntime, ods_truth
from dataquery_agent.query_asset_store import AssetError, QueryAssetStore
from evaluate_dynamic_sql import TODAY, fixed_candidate

QUESTION = "2025年1月各渠道按支付尝试次数列前3订单"
FEBRUARY = QUESTION.replace("1月", "2月").replace("前3", "前2")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "live"), default="live")
    args = parser.parse_args()
    manager, _ = local_manager()
    engine = manager.agent.dynamic_engine
    runtime = QueryAssetRuntime.local(engine)
    admin = QueryAssetStore.local(admin=True)
    governance = QueryAssetGovernance(admin, engine)
    original_snapshot = engine.snapshot
    owner = token_hex(32)
    cid = manager.create_conversation(owner)["conversation_id"]
    backend = manager.generate if args.mode == "live" else fixed_candidate
    report = {"mode": args.mode, "business_data": "real PostgreSQL SQL Shed; independent ODS reference",
              "turns": [], "events": [], "passed": False}

    def turn(question, label, *, assets=True, expected=True):
        manager.agent.asset_runtime = runtime if assets else None
        answer = manager.process_message(owner, cid, question, uuid4(), today=TODAY)
        history = manager.get_query(owner, answer["query_id"])
        truth = ods_truth(recognize(question, TODAY)) if expected else None
        report["turns"].append({"label": label, "question": question, "answer": answer,
                                "history": history, "independent_truth": truth})
        if expected:
            assert answer["status"] == "ok", answer.get("code")
            assert (answer["result"]["results"], answer["result"]["truncated"]) == truth
        return answer, history

    def publish(candidate):
        report["events"].append(candidate)
        ids = candidate["asset_id"], candidate["implementation_version"]
        validated = governance.verify(*ids)
        report["events"].append(validated)
        assert validated["lifecycle_status"] == "VALIDATED", validated
        published = governance.publish(*ids)
        report["events"].append(published)
        return ids

    def no_model(_prompt):
        raise AssertionError("Published asset unexpectedly invoked Gemini")

    try:
        manager.generate = backend
        _, history = turn(QUESTION, "source_dynamic_history", assets=False)
        manager.generate = no_model
        initial = governance.from_history(manager.store, owner, history["query_id"])
        assert initial["lifecycle_status"] == "CANDIDATE", "Use a fresh registry for the lifecycle demo."
        ids = publish(initial)
        _, old_history = turn(QUESTION, "published_v1")
        assert old_history["query_asset"]["implementation_version"] == ids[1]

        drift = deepcopy(original_snapshot())
        columns = drift.get("dwd.payment_attempt_fact")["columns"]
        columns["payment_event_time"] = columns.pop("paid_at")
        engine.snapshot = lambda: drift
        blocked, _ = turn(QUESTION, "synthetic_field_rename_detected_before_execution", expected=False)
        assert blocked["code"] == "asset_metadata_stale" and not blocked["result"]["execution_trace"]["sql_validated"]
        report["events"].append(governance.impact(*ids, mark=True))
        assert report["events"][-1]["lifecycle_status"] == "STALE"
        assert turn(QUESTION, "persisted_stale_blocks_reuse", expected=False)[0]["code"] == "asset_metadata_stale"

        engine.snapshot = original_snapshot
        new_ids = publish(governance.propose(
            (ROOT / "sql/warehouse/query_assets/attempt_rank_by_channel_v2.sql").read_text(),
            {"type": "developer_sql", "reason": "reviewed recovery after simulated drift and restored warehouse contract"}))
        _, recovered = turn(FEBRUARY, "revalidated_recovery_v2")
        assert recovered["query_asset"]["implementation_version"] == new_ids[1]
        _, v2 = turn(QUESTION, "same_window_v2")
        latest = publish(governance.propose(
            (ROOT / "sql/warehouse/query_assets/attempt_rank_by_channel_v3.sql").read_text(),
            {"type": "developer_sql", "reason": "equivalent ranked CTE rename"}))
        _, v3 = turn(QUESTION, "equivalent_sql_v3")
        previous = manager.get_query(owner, v2["query_id"])
        assert previous == v2
        assert v2["query_asset"]["semantic_contract_version"] == v3["query_asset"]["semantic_contract_version"]
        assert v2["query_asset"]["sql_hash"] != v3["query_asset"]["sql_hash"]
        assert v3["query_asset"]["implementation_version"] == latest[1]
        assert admin.read(*new_ids)["lifecycle_status"] == "RETIRED"
        assert manager.get_query(owner, old_history["query_id"]) == old_history
        report["passed"] = True
    except (AssertionError, AssetError) as error:
        report["failure"] = str(error)
    finally:
        print(json.dumps(report, default=str, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
