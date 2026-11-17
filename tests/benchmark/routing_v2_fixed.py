"""Compare captured routing decisions with the same fixed model and tool responses."""
import json
import subprocess
from types import ModuleType
from datetime import date
from unittest.mock import Mock, patch
from dataquery_agent import unified_agent
from dataquery_agent.rag import routing
from dataquery_agent.metric_query_service import MetricQueryService
from benchmark.live_baseline import inventory

def oldmodule(commit, package, path):
    module = ModuleType(package + "._paired_baseline")
    module.__package__ = package
    source = subprocess.check_output(["git", "show", f"{commit}:{path}"], text=True)
    exec(compile(source, path, "exec"), module.__dict__)
    return module

ids = ("exp1:intent:discount", "exp1:intent:account-count", "exp1:sql:returns-above",
       "exp1:planning:cohort-drift", "exp1:pdf:pdf-old-ocr", "exp1:pdf:pdf-original-bbox")
metric_intent = {"metrics":["退款记录超明细净额数"],"time":"2025年9月","groups":["按渠道"],
                 "filters":[],"type":"metric","missing":[]}
empty_intent = {"metrics":[],"time":None,"groups":[],"filters":[],"type":"unknown","missing":[]}

def run(agent_class, route, case_id, question):
    knowledge = Mock()
    knowledge.exact.return_value = {"status":"ok","retrieval_trace":{"strategy":"catalog_exact"}}
    knowledge.answer.return_value = {"status":"ok","retrieval_trace":{"strategy":"pgvector-cosine-exact-v1","trace_id":"fixed"}}
    agent = agent_class(MetricQueryService(None),Mock(),knowledge=knowledge)
    model = Mock(return_value=json.dumps(metric_intent if case_id.endswith("returns-above") else empty_intent))
    with patch.object(routing,"route_knowledge",route), patch.object(agent,"run_metric_request",return_value={"status":"ok"}) as sql:
        answer = agent.answer(question,model,today=date(2026,11,17),knowledge_owner="fixed-owner")
    frame = {"task":answer["task"],"code":answer["code"],"status":answer["status"],
             "metric_ids":answer["result"].get("metric_resolution",{}).get("metric_ids") or (answer.get("match") or {}).get("confirmed",[]),
             "route":next((t.get("strategy") for t in answer["trace"]["tools"] if t.get("strategy")), "metric_query" if sql.called else None),
             "pdf_only":knowledge.answer.called and knowledge.answer.call_args.kwargs["pdf_only"],
             "fixed_intent_calls":model.call_count}
    return frame

def main():
    base = "00ae252"
    old_route = oldmodule(base, "dataquery_agent.rag", "src/dataquery_agent/rag/routing.py")
    old_agent = oldmodule(base, "dataquery_agent", "src/dataquery_agent/unified_agent.py")
    pairs=[]
    for case,_,_ in inventory(authorized_technical=True)[0]:
        if case["case_id"] not in ids:
            continue
        question=case["question"]
        entry={"case_id":case["case_id"],"baseline":run(old_agent.UnifiedAgent,old_route.route_knowledge,case["case_id"],question),
               "candidate":run(unified_agent.UnifiedAgent,routing.route_knowledge,case["case_id"],question)}
        pairs.append(entry)
    print(json.dumps(pairs,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
