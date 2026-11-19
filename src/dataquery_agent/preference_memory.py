"""One explicit preference; scoped structured conditions, never prompt rewriting."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import re

from .intent_semantic_resolver import CHANNELS
from .metric_query_service import RequestError

CHANNEL_SCOPE = ContextVar("default_channel_scope", default=None)


def preference_command(message):
    text = re.sub(r"\s+", "", message).rstrip("。！？!?")
    if text in {"查看默认渠道", "读取默认渠道"}:
        return {"action": "get"}
    if text in {"删除默认渠道", "清除默认渠道"}:
        return {"action": "delete"}
    match = re.fullmatch(r"(?:请)?(?:保存|记住|更新)(?:默认看|默认渠道为)(.+?)(?:渠道)?", text, re.I)
    if match:
        channel = CHANNELS.get(match[1].lower())
        return {"action": "set", "channel": channel} if channel else {"action": "invalid"}
    return None


@contextmanager
def channel_scope(preference, active, message, read_ms, explicit_channel=False):
    token = CHANNEL_SCOPE.set({"preference": preference, "active": active or {},
                               "message": message, "read_ms": read_ms, "structured": False,
                               "explicit_channel": explicit_channel})
    try:
        yield
    finally:
        CHANNEL_SCOPE.reset(token)


def mark_structured(operation):
    if scope := CHANNEL_SCOPE.get():
        scope.update(structured=True, operation=operation)


def channel_request(request, service, trace):
    scope = CHANNEL_SCOPE.get()
    if scope is None:
        return request
    request = deepcopy(request)
    filters = request.setdefault("filters", {})
    active = scope["active"]
    prior = active.get("request", active.get("plan", {})).get("filters", {}).get("channel")
    origin = active.get("channel_origin", "request")
    inherited = (scope["structured"] and not scope["explicit_channel"]
                 and scope.get("operation") not in {"channel", "clarification"})
    explicit = filters.get("channel")
    if inherited and explicit == prior and origin == "preference":
        filters.pop("channel", None)  # Re-read memory; don't freeze a preference as user confirmation.
        explicit = None
    suppressed = origin == "all_channels" or scope.get("operation") == "remove_channel" or bool(
        re.search(r"所有渠道|取消渠道筛选", scope["message"]))
    source, channel = "request", explicit
    if explicit and inherited and explicit == prior:
        source = "conversation"
    if not channel and not suppressed:
        if prior and origin != "preference":
            source, channel = "conversation", prior
        elif scope["preference"]:
            source, channel = "preference", scope["preference"]["channel"]
    event = {"metric_id": request["metric_id"], "channel": channel, "source": source if channel else "none",
             "applied": False, "preference_read_ms": scope["read_ms"]}
    if suppressed and not channel:
        event["suppressed"] = True
    if source == "preference" and channel:
        event["preference_version"] = scope["preference"]["version"]
    if channel and not explicit:
        candidate = dict(request, filters=filters | {"channel": channel})
        try:
            service.validate(candidate)
        except RequestError as error:
            event["reason"] = error.code  # Unsupported implicit condition never widens capability.
        else:
            request = candidate
            event["applied"] = True
    elif channel:
        event["applied"] = True
    trace.setdefault("routing", {}).setdefault("channel_conditions", []).append(event)
    return request


def channel_plan(plan, service, trace):
    if CHANNEL_SCOPE.get() is None:
        return plan
    plan = deepcopy(plan)
    for call in plan["required_tool_calls"]:
        call["request"] = channel_request(call["request"], service, trace)
    channels = {call["request"].get("filters", {}).get("channel") for call in plan["required_tool_calls"]}
    if len(channels) == 1:
        channel = channels.pop()
        plan["filters"] = {k: v for k, v in plan.get("filters", {}).items() if k != "channel"}
        if channel:
            plan["filters"]["channel"] = channel
    return plan
