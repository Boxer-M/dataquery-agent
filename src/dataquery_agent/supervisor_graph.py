"""Bounded delegation over the existing authorized execution paths."""
import json
from typing import TypedDict

from .agent_prompts import BOUNDARY, PROMPT_VERSIONS
from .agent_runtime import ModelUnavailable
from .conversation_context import redact


class GraphState(TypedDict, total=False):
    delegates: list[str]
    approved: list[str]
    result: dict
    interrupt_required: bool
    clarification: dict


def run_supervisor(runtime, trace, *, contexts, execute, saver=None, thread_id=None,
                   clarification=None, resume=None, continuation=None):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt as graph_interrupt
    from langsmith import tracing_context

    events = trace["orchestration"] = []
    roles = list(contexts)
    resumed = []

    def decision(stage, instruction, context, allowed):
        prompt = (
            f"[{PROMPT_VERSIONS[stage]}] {BOUNDARY} {instruction}\n"
            + "仅返回具有 decision 键的 JSON 对象。decision 允许值：" + json.dumps(allowed, ensure_ascii=False)
            + "\n上下文 JSON：" + json.dumps(context, ensure_ascii=False)
        )
        try:
            raw = runtime.call(stage, prompt)
            payload = json.loads(raw) if isinstance(raw, str) and len(raw) <= 256 else None
            choice = payload["decision"] if isinstance(payload, dict) and payload.keys() == {"decision"} else None
            if choice not in allowed:
                raise ValueError("decision outside approved scope")
        except ModelUnavailable:
            events.append({"node": stage, "status": "unavailable"})
            return None
        except (ValueError, RecursionError):
            runtime.invalid_output()
            events.append({"node": stage, "status": "rejected"})
            return None
        events.append({"node": stage, "status": "accepted", "decision": choice})
        return choice

    def supervisor(state):
        if pending := state.get("clarification"):
            choice = decision("supervisor", "已发现正式指标口径歧义，必须请用户选口径。",
                              {"request": pending["original_question"], "choices": pending["choices"]},
                              ["clarify"])
            return {"delegates": [], "interrupt_required": choice == "clarify"}

        options = ["clarify", *roles]
        if len(roles) > 1:
            options.append("both")
        choice = decision("supervisor", "将问题拆成指标分析和文档证据任务。联合请求应委派两方；范围缺失时澄清。",
                          {role: contexts[role]["request"] for role in roles}, options)
        return {"delegates": roles if choice == "both" else [choice] if choice in roles else [],
                "interrupt_required": choice == "clarify",
                **({"result": {"status": "error", "code": "invalid_supervisor_decision",
                               "answer_zh": "协调请求暂不可用。"}} if choice is None else {})}

    def specialist(role, instruction):
        def node(state):
            if role not in state["delegates"]:
                return {}
            choice = decision(role, instruction, contexts[role], ["execute", "defer"])
            return {"approved": [*state["approved"], role]} if choice == "execute" else {}
        return node

    def interrupt(state):
        if pending := state.get("clarification"):
            chosen = graph_interrupt({"slot": "metric_id"})
            if resumed:
                raise RuntimeError("Repeated continuation in the same Graph invocation")
            resumed.append(continuation(pending, chosen))
            return {"interrupt_required": False, "clarification": {}}
        return {"result": {"status": "clarify", "code": "supervisor_clarification_required",
                           "answer_zh": "请补充要分析的指标、时间范围或要引用的具体 PDF 主题。"}}

    def run_tools(state):
        return {"result": execute(frozenset(state["approved"]))}

    def insight(state):
        result = state["result"]
        has_partial_evidence = result["status"] == "partial"
        choice = decision("investigation", "已有部分核验结果。判断是否应请求用户补充证据范围。保留已核验结果。",
                          {"status": "partial", "code": result.get("code")},
                          ["finish", "clarify"]) if has_partial_evidence else None
        events.append({"node": "insight", "status": result["status"],
                       "next_action": choice or "finish"})
        answer = result.get("answer_zh", "")
        if result.get("path") == "sql_document_hybrid" and result["status"] == "ok":
            answer += "\n指标数值适用所查时间窗口，PDF 引用说明文档规则。现有证据只支持分别陈述这两项。"
        if choice == "clarify":
            answer += "\n如需继续，请补充失败部分的查询范围或证据来源。"
        return {"interrupt_required": choice == "clarify",
                "result": dict(result, answer_zh=answer) if answer else result}

    graph = StateGraph(GraphState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("data", specialist("data", "核对正式计划能否覆盖指标问题。批准该计划或保留待澄清。"))
    graph.add_node("knowledge_agent", specialist(
        "knowledge_agent", "判断主题能否用已审核 PDF 的原文回答。批准限定范围检索或保留待澄清。"))
    graph.add_node("interrupt", interrupt)
    graph.add_node("tools", run_tools)
    graph.add_node("insight", insight)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", lambda state: (
        "interrupt" if state["interrupt_required"] else "data" if state["delegates"] else END),
        ["interrupt", "data", END])
    graph.add_edge("interrupt", END)
    graph.add_edge("data", "knowledge_agent")
    graph.add_edge("knowledge_agent", "tools")
    graph.add_edge("tools", "insight")
    graph.add_edge("insight", END)
    with tracing_context(enabled=False):
        compiled = graph.compile(checkpointer=saver)
        config = {"recursion_limit": 9}
        if thread_id is not None:
            config["configurable"] = {"thread_id": thread_id}
        initial = {"approved": [], **({"clarification": clarification} if clarification else {})}
        final = compiled.invoke(Command(resume=resume) if resume is not None else initial, config)
    if resume is not None:
        if len(resumed) != 1:
            raise RuntimeError("Resume did not execute the pending continuation")
        return resumed[0]
    if final.get("__interrupt__"):
        trace["interrupt_required"] = True
        return {"paused": True}
    trace["interrupt_required"] = final["interrupt_required"]
    return final.get("result", {})


def role_context(request, **scope):
    return {"request": redact(request), **scope}
