"""Persist formal metric clarification using the original query history record."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from time import monotonic

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .agent_runtime import AgentRuntime
from .conversation_context import ContextError, resume_request
from .conversation_store import ConversationError, identifier, owner_hash
from .query_history import build_record, replay_summary
from .supervisor_graph import run_supervisor


@contextmanager
def checkpointer(store):
    from langgraph.checkpoint.postgres import PostgresSaver

    with psycopg.connect(store.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5,
                         options="-c search_path=agent_checkpoint,pg_catalog -c statement_timeout=5000") as pg:
        yield PostgresSaver(pg)


def catalog_hash(manager):
    return sha256(json.dumps(manager.agent.service.catalog, sort_keys=True).encode()).hexdigest()


def pause(manager, answer, pending, run_id):
    runtime = AgentRuntime(manager.generate)
    trace = answer["trace"]
    with checkpointer(manager.store) as saver:
        result = run_supervisor(runtime, trace, contexts={}, execute=None, saver=saver,
                                thread_id=str(run_id), clarification=pending)
    trace.setdefault("model_attempts", []).extend(runtime.attempts)
    if not result.get("paused"):
        return False
    answer["hitl"] = {"status": "paused", "expires_at": pending["expires_at"],
                      "prompt": "请选择本次查询的正式指标口径。", "choices": pending["choices"],
                      "events": [event("paused")]}
    return True


def event(status, **details):
    return {"status": status, "at": datetime.now(timezone.utc).isoformat(), **details}


def update_resume(manager, owner, run_id, selection):
    run_id, owner_key = identifier(run_id), owner_hash(owner)
    with manager.store.transaction() as pg:
        row = pg.execute("""
            SELECT h.record, h.started_at, h.conversation_id, h.message_id
            FROM agent_meta.query_history h JOIN agent_meta.conversations c USING (conversation_id)
            WHERE h.query_id=%s AND c.owner_hash=%s FOR UPDATE OF h
        """, (run_id, owner_key)).fetchone()
        if row is None:
            raise ConversationError("query_not_found")
        prior = row["record"]
        hitl = prior.get("hitl", {})
        if hitl.get("status") != "paused":
            raise ConversationError("resume_not_pending")
        rejection = None
        if datetime.fromisoformat(hitl["expires_at"]) <= datetime.now(timezone.utc):
            rejection = "clarification_expired"
            hitl["status"] = "expired"
        elif selection not in {choice["metric_id"] for choice in hitl["choices"]}:
            rejection = "invalid_clarification_choice"
        if rejection:
            if len(hitl["events"]) < 6:
                hitl["events"].append(event("rejected", code=rejection))
        else:
            hitl["status"] = "resuming"
            hitl["events"].append(event("resuming", metric_id=selection))
        pg.execute("UPDATE agent_meta.query_history SET record=%s WHERE query_id=%s", (Jsonb(prior), run_id))
    if rejection:
        raise ConversationError(rejection)

    started = monotonic()
    from .conversation_manager import response

    def continuation(pending, chosen):
        manager.store.history(owner, run_id)
        if pending["catalog_hash"] != catalog_hash(manager):
            raise ContextError("catalog_changed", "指标目录已变更。")
        request = resume_request(pending, chosen, manager.agent.semantic)
        return manager.structured("clarification", request)

    try:
        with checkpointer(manager.store) as saver:
            answer = run_supervisor(AgentRuntime(manager.generate), {}, contexts={}, execute=None,
                                    saver=saver, thread_id=str(run_id), resume=selection,
                                    continuation=continuation)
    except ConversationError as error:
        answer = response("error", error.code, "当前运行已不可访问。")
    except ContextError as error:
        answer = response("error", error.code, "当前口径或权限已变化。")
    except Exception:
        answer = response("error", "resume_failed", "恢复未完成。")

    status = "completed" if answer["status"] == "ok" else "failed"
    hitl.update(status=status, answer=answer.get("answer_zh"))
    hitl["events"].append(event(status, **({"code": answer["code"]} if answer.get("code") else {})))
    trace = answer["trace"]
    trace.update(trace_id=prior["trace_id"], orchestration=prior.get("orchestration", []),
                 interrupt_required=False)
    answer["hitl"] = hitl
    record = build_record(row, prior["original_question"], answer, "hitl_resume",
                          prior["latency_ms"] + round((monotonic() - started) * 1000), query_id=run_id)
    record["prompt_versions"] = prior["prompt_versions"]
    with manager.store.transaction() as pg:
        changed = pg.execute("""
            UPDATE agent_meta.query_history h SET record=%s, completed_at=now()
            FROM agent_meta.conversations c
            WHERE h.query_id=%s AND h.conversation_id=c.conversation_id AND c.owner_hash=%s
              AND h.record->'hitl'->>'status'='resuming'
        """, (Jsonb(record), run_id, owner_key))
        if changed.rowcount != 1:
            raise ConversationError("query_not_found")
        pg.execute("""UPDATE agent_meta.conversations SET active_query_context=%s,
                          state_version=state_version+1, updated_at=now()
                      WHERE conversation_id=%s AND owner_hash=%s AND last_query_id=%s AND in_flight IS NULL""",
                   (Jsonb(manager.context_for(answer)), row["conversation_id"], owner_key, run_id))
        pg.execute("""UPDATE agent_meta.messages SET response=%s
                      WHERE conversation_id=%s AND message_id=%s AND status='completed'""",
                   (Jsonb(replay_summary(record, answer)), row["conversation_id"], row["message_id"]))
    return dict(answer, query_id=str(run_id), conversation_id=row["conversation_id"], message_id=row["message_id"])
