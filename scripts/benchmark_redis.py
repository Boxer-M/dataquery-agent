"""Run comparable loopback HTTP batches against real SQL Shed and Redis."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from math import ceil
from pathlib import Path
import platform
import re
from secrets import token_hex
import socket
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx2
import uvicorn

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.performance import Performance

QUESTIONS = [f"2025年{month}月{metric}是多少？"
             for metric in ("订单量", "订单商品明细数") for month in range(1, 13)]
COUNTERS = ("hits", "misses", "sql", "acl", "model_calls", "model_tokens")


def fixed_intent(prompt):
    question = json.loads(prompt.rsplit("用户原文 JSON：", 1)[1])
    metric = "订单商品明细数" if "订单商品明细数" in question else "订单量"
    return json.dumps({"metrics": [metric], "time": re.search(r"2025年\d+月", question).group(),
                       "groups": [], "filters": [], "type": "metric", "missing": []})


def stats(rows, elapsed):
    latencies = sorted(row["ms"] for row in rows)
    completed = sum(row["status"] == "completed" for row in rows)
    metrics = {key: sum(row[key] for row in rows) for key in COUNTERS}
    cache_accesses = metrics["hits"] + metrics["misses"]
    return dict(metrics, requests=len(rows), completed=completed, elapsed_s=round(elapsed, 3),
                completed_qps=round(completed / elapsed, 2), response_qps=round(len(rows) / elapsed, 2),
                p50_ms=latencies[ceil(.5 * len(rows)) - 1], p95_ms=latencies[ceil(.95 * len(rows)) - 1],
                hits_per_completed=round(metrics["hits"] / completed, 4) if completed else None,
                cache_hit_rate=round(metrics["hits"] / cache_accesses, 4) if cache_accesses else None,
                http_errors=sum(row["http"] >= 400 for row in rows),
                http_error_rate=round(sum(row["http"] >= 400 for row in rows) / len(rows), 4),
                http_429=sum(row["http"] == 429 for row in rows),
                http_429_by_code=dict(Counter(row["error"] for row in rows if row["http"] == 429)),
                application_errors=sum(row["http"] == 200 and row["status"] != "completed" for row in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="redis://127.0.0.1:6381/0")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    owner, token, namespace = token_hex(32), token_hex(32), uuid4().hex
    workload = Performance(url=args.url, data_version=namespace, report=True)
    if not workload.redis.ping():
        raise RuntimeError("Redis unavailable")
    redis_client = workload.redis

    def manager_factory():
        manager, _ = local_manager()
        manager.generate = fixed_intent
        return manager, owner

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(token=token, manager_factory=manager_factory,
                                                      performance=workload), access_log=False, log_level="warning"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        with httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=20,
                           headers={"Authorization": f"Bearer {token}"}) as http:
            for _ in range(200):
                if server.started:
                    break
                sleep(.02)

            def conversations():
                return [http.post("/api/conversations").raise_for_status().json()["conversation_id"]
                        for _ in QUESTIONS]

            ids = conversations()

            def query(index):
                started = monotonic()
                response = http.post("/api/chat", json={"conversation_id": ids[index],
                                     "message_id": str(uuid4()), "question": QUESTIONS[index]})
                payload = response.json()
                if response.status_code == 429 and int(response.headers["Retry-After"]) < 1:
                    raise AssertionError("Invalid Retry-After")
                row = {key: int(response.headers.get("X-DataQuery-" + key.replace("_", "-"), "0"))
                       for key in COUNTERS}
                return dict(row, index=index, http=response.status_code, status=payload.get("status"),
                            error=(payload.get("error") or {}).get("code"), rows=payload.get("rows"),
                            ms=round((monotonic() - started) * 1000, 3))

            def batch(concurrency, repeats, prepare=None):
                items, seconds = [], 0
                for repeat in range(repeats):
                    if prepare:
                        prepare(repeat)
                    with ThreadPoolExecutor(max_workers=concurrency) as workers:
                        started = monotonic()
                        items.extend(workers.map(query, range(len(QUESTIONS))))
                        seconds += monotonic() - started
                return items, stats(items, seconds)

            batch(1, 1)  # Warm Python, HTTP and connections outside measurement.
            baseline, results = {}, []
            for concurrency in (1, 5, 10, 20):
                for mode in ("off", "cold", "hot"):
                    workload.redis = None if mode == "off" else redis_client
                    if mode == "hot":
                        workload.data_version = namespace + "-hot"
                        batch(5, 1)
                    prepare = None
                    if mode == "cold":
                        def prepare(trial):
                            workload.data_version = f"{namespace}-cold-{concurrency}-{trial}"
                    rows, metrics = batch(concurrency, args.repeat, prepare)
                    for row in rows:
                        if row["status"] == "completed":
                            expected = baseline.setdefault(row["index"], row["rows"])
                            if row["rows"] != expected:
                                raise AssertionError("Metric response changed between modes")
                    results.append(dict(metrics, mode=mode, concurrency=concurrency))
                    print(results[-1], flush=True)

            owner = token_hex(32)
            ids = conversations()
            workload.limit = 12
            workload.window = 30
            _, metrics = batch(20, 1)
            results.append(dict(metrics, mode="rate_limit", concurrency=20))
            print(results[-1], flush=True)

        args.out.write_text(json.dumps({
            "python": platform.python_version(), "system": platform.platform(),
            "redis": redis_client.info("server")["redis_version"],
            "sqlshed": "existing local PostgreSQL warehouse", "queries": QUESTIONS,
            "repeat": args.repeat, "generation": "fixed intent callback, no external calls",
            "definitions": {"sql": "actual metric statements, excludes metadata/history transactions",
                            "acl": "actual extra metric login permission probe statements",
                            "model_calls": "instrumented callback invocations, not external LLM"},
            "results": results,
        }, ensure_ascii=False, indent=2) + "\n")
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()


if __name__ == "__main__":
    main()
