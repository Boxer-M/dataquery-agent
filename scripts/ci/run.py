"""Run existing opt-in tests and a real HTTP smoke test in disposable Compose."""
import json
import os
from pathlib import Path
from secrets import token_hex
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
from urllib.request import Request, urlopen
from uuid import uuid4
import xml.etree.ElementTree as ET

import uvicorn

from bootstrap import bootstrap

MODULES = ("warehouse_postgres", "ads", "dws", "metric_query_service", "metadata_capability",
           "detail_executor", "dynamic_sql_engine", "business_planner_v2", "business_knowledge",
           "metric_exact_routing", "task_dag", "specialists", "catalog_recovery",
           "capability_trace_linkage", "api", "conversation", "conversation_history_api", "preference_memory", "explicit_date_windows", "hitl", "redis_performance", "skill_business", "artifact_api")
FLAGS = ("WAREHOUSE", "METRIC_QUERY", "CAPABILITY", "DETAIL", "DYNAMIC", "KNOWLEDGE", "API",
         "CONVERSATION", "HITL", "REDIS")


def smoke():
    from dataquery_agent.api import create_app
    from dataquery_agent.conversation_manager import local_manager
    from test_api import INTENT

    manager, owner = local_manager()
    manager.generate = lambda _: json.dumps(INTENT)
    token = token_hex(32)
    server = uvicorn.Server(uvicorn.Config(create_app(token=token, manager_factory=lambda: (manager, owner)),
                                          host="127.0.0.1", port=8000, log_level="error", access_log=False))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        until = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < until:
            sleep(.05)
        if not server.started:
            raise RuntimeError("API failed to start")

        def request(path, data=None):
            body = json.dumps(data).encode() if data is not None else None
            req = Request("http://127.0.0.1:8000/api/" + path, data=body,
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urlopen(req, timeout=20) as response:
                return json.load(response)

        assert request("health") == {"status": "ok"}
        cid = request("conversations", {})["conversation_id"]
        answer = request("chat", {"conversation_id": cid, "message_id": str(uuid4()),
                                  "question": "2025年1月订单量是多少？"})
        assert answer["status"] == "completed" and answer["rows"][0]["value"] == "2312"
        assert request("queries/" + answer["query_id"])["trace"] == answer["trace"]
        return {"mode": "fixed intent, real HTTP and PostgreSQL", "health": "ok", "order_count": "2312",
                "history_trace_equal": True, "external_model_calls": 0,
                "source_commit": os.getenv("CI_SOURCE_SHA", "local")}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main():
    bootstrap()
    destination = Path("artifacts")
    destination.mkdir(exist_ok=True)
    env = os.environ | {f"{name}_POSTGRES_TESTS": "1" for name in FLAGS}
    env["CI_NO_EXTERNAL_MODELS"] = "1"
    subprocess.run([sys.executable, "-m", "pytest", *[f"tests/test_{name}.py" for name in MODULES],
                    "--junitxml=artifacts/integration.xml"], env=env, check=True)
    cases = list(ET.parse(destination / "integration.xml").iter("testcase"))
    for name in ("HitlTests", "RedisTests", "DagPostgresTests", "ExactRoutingPostgresTests"):
        selected = [case for case in cases if name in case.get("classname", "") and case.find("skipped") is None]
        if not selected:
            raise RuntimeError(f"Required integration suite did not execute: {name}")
    (destination / "smoke.json").write_text(json.dumps(smoke(), indent=2) + "\n")
    print("Existing integration tests and real HTTP smoke passed", flush=True)


if __name__ == "__main__":
    main()
