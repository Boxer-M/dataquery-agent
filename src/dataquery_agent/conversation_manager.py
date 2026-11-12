"""Coordinate context, Unified Agent execution and owner-scoped query history."""
import argparse
import json
from datetime import datetime
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from dotenv import dotenv_values

from .conversation_context import (
    CONTEXT_VERSION, RESET, ContextError, edit_for_message, pending_for, redact, resume_request,
)
from .conversation_store import ConversationError, ConversationStore
from .intent_time import TIME
from .metrics import ROOT
from .query_history import build_record, replay_summary


def response(status, code, message):
    return {"status": status, "code": code, "task": "conversation", "answer_zh": message,
            "result": {}, "routes": [], "choices": [],
            "trace": {"trace_id": uuid4().hex, "model_attempts": [], "tools": []}}


class ConversationManager:
    def __init__(self, store, agent, generate):
        self.store = store
        self.agent = agent
        self.generate = generate

    def create_conversation(self, owner):
        return dict(self.store.create(owner), conversation_status="active")

    def get_conversation(self, owner, conversation_id):
        return self.store.get(owner, conversation_id)

    def get_query(self, owner, query_id):
        return self.store.history(owner, query_id)

    def process_message(self, owner, conversation_id, message, message_id, *, today=None):
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            return response("clarify", "invalid_message", "请提供不超过 500 字的业务消息。")
        try:
            claim = self.store.claim(owner, conversation_id, message_id, message)
        except ConversationError as error:
            return response("error", error.code, "会话不可用、正在处理或消息标识冲突。")
        except psycopg.Error:
            return response("error", "management_unavailable", "暂时无法保存会话消息。")
        ids = {"conversation_id": str(conversation_id), "message_id": str(message_id)}
        if "replay" in claim:
            return {**claim["replay"], **ids, "replayed": True}

        began = monotonic()
        today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        try:
            answer, context, pending, operation = self.dispatch(message, claim["state"], today, owner=owner)
            record = build_record(claim, message, answer, operation, round((monotonic() - began) * 1000))
        except Exception:
            answer = response("error", "agent_failed", "查询处理失败，请稍后重试。")
            context = claim["state"]["active_query_context"]
            pending = claim["state"]["pending_clarification"]
            record = build_record(claim, message, answer, "failed", round((monotonic() - began) * 1000))
        try:
            self.store.finish(claim, context, pending, record, replay_summary(record, answer))
        except (psycopg.Error, ConversationError) as error:
            try:
                self.store.abort(claim)
            except (psycopg.Error, ConversationError):
                pass  # The bounded lease still prevents permanent lockout.
            code = error.code if isinstance(error, ConversationError) else "history_write_failed"
            return {**response("error", code, "查询状态和历史未提交，请使用新消息重试。"), **ids}
        return {**answer, **ids, "query_id": record["query_id"], "context_operation": record["context_operation"],
                "state_version": claim["state"]["state_version"] + 1, "replayed": False}

    def dispatch(self, message, state, today, *, owner=None):
        active = state["active_query_context"]
        pending = state["pending_clarification"]
        if redact(message) != message:
            return response("unsupported", "sensitive_input", "请去除凭据或个人信息后重试。"), active, pending, "rejected"
        if message.strip().rstrip("。！？!?") in RESET:
            return response("ok", None, "已清空本会话的查询条件。"), None, None, "reset"
        query = message
        if getattr(self.agent, "knowledge", None) and not pending:
            from .rag.routing import knowledge_followup
            query = knowledge_followup(message, active)
        try:
            edit = edit_for_message(query, active, self.agent.service.metrics,
                                    self.agent.service.cutoff.date().isoformat()) if not pending else None
            if pending and not self.is_new_topic(message):
                request = resume_request(pending, message, self.agent.semantic)
                operation, payload = "clarification", request
            elif edit:
                operation, payload = edit
            else:
                if getattr(self.agent, "knowledge", None):
                    answer = self.agent.answer(query, self.generate, today=today, knowledge_owner=owner)
                else:
                    answer = self.agent.answer(message, self.generate, today=today)
                if answer.get("task") == "blocked":
                    return answer, active, pending, "rejected"
                new_pending = pending_for(answer, message, today, self.agent.semantic)
                return answer, self.context_for(answer), new_pending, "knowledge_followup" if query != message else "new"
        except ContextError as error:
            keep = None if error.code == "clarification_expired" else pending
            answer = response("clarify", error.code, str(error))
            if keep:
                answer["choices"] = keep["choices"]
            return answer, active, keep, "clarify"
        answer = self.structured(operation, payload)
        if answer["status"] == "ok":
            updated = self.context_for(answer)
            if operation == "comparison":
                updated = dict(active, last_comparison=payload["time_windows"])
            return answer, updated, None, operation
        return answer, active, pending, operation

    @staticmethod
    def is_new_topic(message):
        from .conversation_context import MONTH

        text = message.strip().rstrip("。！？!?")
        remainder = TIME.sub("", text).strip("的？?呢，, ")
        return not MONTH.fullmatch(text) and bool(TIME.search(text)) and len(remainder) >= 2

    def structured(self, operation, payload):
        started = monotonic()
        answer = response("ok", None, "")
        answer["task"] = "business" if operation == "comparison" else "single"
        frame = {"routes": answer["routes"], "event_time_hint": None}
        trace = answer["trace"]
        if operation == "comparison":
            result = self.agent.run_business_plan(payload, frame, trace)
        else:
            result = self.agent.run_metric_request(payload, frame, trace)
        trace["elapsed_ms"] = round((monotonic() - started) * 1000)
        answer.update(status=result["status"], code=result.get("code"), result=result,
                      answer_zh=result.get("answer_zh") or result.get("message") or "")
        return answer

    @staticmethod
    def context_for(answer):
        if answer["status"] != "ok":
            return None
        result, routes = answer["result"], answer.get("routes", [])
        if "query_result" in result and len(routes) == 1 and routes[0].get("metric_request"):
            return {"kind": "standard", "context_protocol": CONTEXT_VERSION, "request": routes[0]["metric_request"],
                    "confirmed_definition": result["query_result"].get("definition"),
                    "source_models": result["query_result"].get("sources", []),
                    "metadata_version": routes[0].get("metadata_version")}
        if "plan" in result:
            return {"kind": "business", "context_protocol": CONTEXT_VERSION, "plan": result["plan"]}
        if "detail_result" in result:
            detail = result["detail_result"]
            return {"kind": "detail", "context_protocol": CONTEXT_VERSION, "analysis_id": detail["analysis_id"],
                    "time_constraints": detail["time_constraints"], "source_models": detail["sources"]}
        if result.get("path") == "knowledge":
            return {"kind": "knowledge", "context_protocol": CONTEXT_VERSION, "topic": result["knowledge_topic"]}
        if result.get("path") in {"dynamic_sql", "published_asset"}:
            return {"kind": "dynamic", "context_protocol": CONTEXT_VERSION, "operation": result["operation"],
                    "parameters": result["parameters"], "source_models": result["sources"]}
        return None


def local_manager():
    from .agent_prompts import schema_for_prompt
    from .capability_registry import CapabilityRegistry
    from .capability_resolver import CapabilityResolver
    from .detail_executor import DetailQueryExecutor
    from .dynamic_sql_engine import DynamicSqlEngine
    from .intent_semantic_resolver import gemini_generator
    from .metadata_service import MetadataService
    from .metric_query_service import MetricQueryService
    from .query_asset_governance import QueryAssetRuntime
    from .rag.answer import KnowledgeService
    from .unified_agent import UnifiedAgent

    cfg = dotenv_values(ROOT / ".env.agent-meta")
    store = ConversationStore(cfg.get("AGENT_META_DSN"))
    capability = CapabilityResolver(CapabilityRegistry(MetadataService.local().snapshot()))
    service = MetricQueryService(dotenv_values(ROOT / ".env.metric-query").get("METRIC_QUERY_DSN"))
    engine = DynamicSqlEngine.local()
    agent = UnifiedAgent(service, capability, DetailQueryExecutor.local(), engine,
                         QueryAssetRuntime.local(engine), KnowledgeService.local(service.metrics))
    model = gemini_generator(dotenv_values(ROOT / ".env"), include_usage=True, schema_for_prompt=schema_for_prompt)
    return ConversationManager(store, agent, model), cfg.get("AGENT_OWNER_TOKEN")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create")
    sub.add_parser("prune")
    state = sub.add_parser("state")
    state.add_argument("conversation_id")
    history = sub.add_parser("history")
    history.add_argument("query_id")
    send = sub.add_parser("send")
    send.add_argument("conversation_id")
    send.add_argument("message")
    send.add_argument("--message-id")
    args = parser.parse_args()
    try:
        if args.command == "send":
            manager, owner = local_manager()
            result = manager.process_message(owner, args.conversation_id, args.message, args.message_id or uuid4())
        else:
            cfg = dotenv_values(ROOT / ".env.agent-meta")
            store, owner = ConversationStore(cfg.get("AGENT_META_DSN")), cfg.get("AGENT_OWNER_TOKEN")
            if args.command == "create":
                result = dict(store.create(owner), conversation_status="active")
            elif args.command == "state":
                result = store.get(owner, args.conversation_id)
            elif args.command == "history":
                result = store.history(owner, args.query_id)
            else:
                result = store.prune(owner)
    except (psycopg.Error, ValueError, OSError, ConversationError) as error:
        result = response("error", error.code if isinstance(error, ConversationError) else "management_unavailable",
                          "无法读取会话管理服务。")
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
