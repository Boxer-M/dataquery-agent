"""Run comparable loopback HTTP batches against real SQL Shed and Redis."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
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
from dataquery_agent.catalog_recovery import sql_checkpoint
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.intent_time import TIME, date_window
from dataquery_agent.performance import Performance

QUESTIONS = [f"2025年{month}月{metric}是多少？"
             for metric in ("订单量", "订单商品明细数") for month in range(1, 13)]
STAGES = ("setup", "route", "capability", "redis", "acl", "sql-tool", "sql-statements", "assembly")
COUNTERS = ("hits", "misses", "sql", "acl", "model_calls", "model_tokens")


def fixed_intent(prompt):
    question = json.loads(prompt.rsplit("用户原文 JSON：", 1)[1])
    names = ("折扣后商品销售金额", "订单商品明细数", "订单量")
    metric = next(name for name in names if name in question)
    return json.dumps({"metrics": [metric], "time": TIME.search(question).group(),
                       "groups": re.findall("按月|按品类", question), "filters": [],
                       "type": "metric", "missing": []})


def workloads(size):
    days = [date(2025, 1, 1) + timedelta(days=index) for index in range(size)]
    return {
        "simple": [f"{day}订单量是多少？" for day in days],
        "aggregate": [f"从{day}到{day + timedelta(days=179)}按月按品类查询折扣后商品销售金额"
                      for day in days],
    }


def snapshot_proof(questions):
    manager, _ = local_manager()
    requests = []
    for name, cases in questions.items():
        mid = "order_count" if name == "simple" else "item_net_sales_amount"
        start, end = date_window(TIME.search(cases[0]).group(), date(2025, 1, 1))
        requests.append({"metric_id": mid, "start_date": start.isoformat(), "end_date": end.isoformat(),
                         "group_by": [] if name == "simple" else ["month", "category"]})
    plan = {"required_tool_calls": [{"request": request} for request in requests],
            "metric_ids": [request["metric_id"] for request in requests]}
    routes = [manager.agent.capability.resolve(request) for request in requests]
    proof = sql_checkpoint(manager.agent.service, plan, routes)
    if not proof:
        raise RuntimeError("Could not prove the warehouse snapshot")
    return proof


def stats(rows, elapsed):
    latencies = sorted(row["ms"] for row in rows)
    completed = sum(row["status"] == "completed" for row in rows)
    metrics = {key: sum(row[key] for row in rows) for key in COUNTERS}
    cache_accesses = metrics["hits"] + metrics["misses"]
    stage_summary = {}
    for stage in STAGES:
        values = sorted(row["stages"][stage] for row in rows
                        if row["status"] == "completed" and stage in row["stages"])
        stage_summary[stage] = {"total_ms": round(sum(row["stages"].get(stage, 0) for row in rows), 3),
                                "completed_samples": len(values),
                                "p50_ms": values[ceil(.5 * len(values)) - 1] if values else None,
                                "p95_ms": values[ceil(.95 * len(values)) - 1] if values else None}
    return dict(metrics, requests=len(rows), completed=completed, elapsed_s=round(elapsed, 3),
                completed_qps=round(completed / elapsed, 2), response_qps=round(len(rows) / elapsed, 2),
                p50_ms=latencies[ceil(.5 * len(rows)) - 1], p95_ms=latencies[ceil(.95 * len(rows)) - 1],
                hits_per_completed=round(metrics["hits"] / completed, 4) if completed else None,
                cache_hit_rate=round(metrics["hits"] / cache_accesses, 4) if cache_accesses else None,
                http_errors=sum(row["http"] >= 400 for row in rows),
                http_error_rate=round(sum(row["http"] >= 400 for row in rows) / len(rows), 4),
                http_429=sum(row["http"] == 429 for row in rows),
                http_429_by_code=dict(Counter(row["error"] for row in rows if row["http"] == 429)),
                http_5xx_rate=round(sum(row["http"] >= 500 for row in rows) / len(rows), 4),
                transport_errors=sum(row["http"] == 0 for row in rows),
                http_429_rate=round(sum(row["http"] == 429 for row in rows) / len(rows), 4),
                stages=stage_summary,
                application_errors=sum(row["http"] == 200 and row["status"] != "completed" for row in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="redis://127.0.0.1:6381/0")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--v2", action="store_true")
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument("--workload", choices=("simple", "aggregate"))
    parser.add_argument("--levels", type=int, nargs="+")
    parser.add_argument("--round-pause", type=float, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or not 1 <= args.samples <= 365:
        parser.error("--repeat must be positive and --samples must be between 1 and 365")
    if args.round_pause < 0:
        parser.error("--round-pause cannot be negative")
    if args.levels and not all(1 <= level <= 20 for level in args.levels):
        parser.error("--levels must be between 1 and 20")
    available = workloads(args.samples) if args.v2 else {"simple": QUESTIONS}
    if args.workload and args.workload not in available:
        parser.error("The aggregate workload requires --v2")
    profiles = {args.workload: available[args.workload]} if args.workload else available
    levels = args.levels or ([1, 5, 8] if args.v2 else [1, 5, 10, 20])
    proof = snapshot_proof(available) if args.v2 else None
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

            def conversations(questions):
                return [http.post("/api/conversations").raise_for_status().json()["conversation_id"]
                        for _ in questions]

            def query(index):
                started = monotonic()
                try:
                    response = http.post("/api/chat", json={"conversation_id": ids[index],
                                         "message_id": str(uuid4()), "question": questions[index]})
                except httpx2.TransportError as error:
                    return dict.fromkeys(COUNTERS, 0) | {
                        "index": index, "http": 0, "status": "transport_error", "stages": {},
                        "error": type(error).__name__, "ms": round((monotonic() - started) * 1000, 3),
                    }
                payload = response.json()
                if response.status_code == 429 and int(response.headers["Retry-After"]) < 1:
                    raise AssertionError("Invalid Retry-After")
                row = {key: int(response.headers.get("X-DataQuery-" + key.replace("_", "-"), "0"))
                       for key in COUNTERS}
                stages = {stage: float(response.headers[f"x-dataquery-{stage}-ms"]) for stage in STAGES
                          if f"x-dataquery-{stage}-ms" in response.headers}
                return dict(row, index=index, http=response.status_code, status=payload.get("status"), stages=stages,
                            error=(payload.get("error") or {}).get("code"), rows=(payload.get("rows"), payload.get("row_count"), payload.get("rows_truncated")),
                            ms=round((monotonic() - started) * 1000, 3))

            def batch(concurrency, repeats, prepare=None):
                items, seconds = [], 0
                for repeat in range(repeats):
                    if prepare:
                        prepare(repeat)
                    with ThreadPoolExecutor(max_workers=concurrency) as workers:
                        started = monotonic()
                        items.extend(workers.map(query, range(len(questions))))
                        seconds += monotonic() - started
                return items, stats(items, seconds)

            results = []
            for name, questions in profiles.items():
                ids = conversations(questions)
                workload.redis = None
                batch(5, 1)
                baseline, profiles_by_key = {}, {}
                for repeat in range(args.repeat):
                    if repeat and args.round_pause:
                        sleep(args.round_pause)
                    for level in levels:
                        modes = ["off", "cold", "hot"]
                        if args.v2 and repeat:
                            modes = modes[repeat % 3:] + modes[:repeat % 3]
                        for mode in modes:
                            workload.redis = None if mode == "off" else redis_client
                            workload.data_version = f"{namespace}-{name}-{level}-{repeat}-{mode}"
                            if mode == "hot":
                                warmed, _ = batch(5, 1)
                                if any(row["status"] != "completed" for row in warmed):
                                    raise AssertionError("Hot run setup did not fill every cache key")
                            rows, metrics = batch(level, 1)
                            if args.v2 and (metrics["http_5xx_rate"] > .05
                                            or metrics["transport_errors"] > len(rows) * .05):
                                raise RuntimeError("Aborting after more than 5% server or transport errors")
                            for row in rows:
                                if row["status"] == "completed":
                                    expected = baseline.setdefault(row["index"], row["rows"])
                                    if row["rows"] != expected:
                                        raise AssertionError("Metric response changed between modes")
                            combined, rounds = profiles_by_key.setdefault((level, mode), ([], []))
                            combined.extend(rows)
                            rounds.append(metrics)
                            print(name, level, mode, repeat + 1, metrics["completed"], metrics["elapsed_s"], flush=True)
                for (level, mode), (rows, rounds) in profiles_by_key.items():
                    results.append(dict(stats(rows, sum(item["elapsed_s"] for item in rounds)),
                                        mode=mode, concurrency=level, workload=name, rounds=rounds))

            owner = token_hex(32)
            questions = QUESTIONS
            ids = conversations(questions)
            workload.limit = 12
            workload.window = 30
            _, metrics = batch(20, 1)
            results.append(dict(metrics, mode="rate_limit", concurrency=20))
            print(results[-1], flush=True)

        if proof is not None and snapshot_proof(available) != proof:
            raise AssertionError("Warehouse snapshot changed during the comparison")
        args.out.write_text(json.dumps({
            "python": platform.python_version(), "system": platform.platform(),
            "redis": redis_client.info("server")["redis_version"],
            "sqlshed": "existing local PostgreSQL warehouse", "snapshot_proof": proof, "queries": profiles if args.v2 else QUESTIONS,
            "repeat": args.repeat, "round_pause_s": args.round_pause, "generation": "fixed intent callback, no external calls",
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
