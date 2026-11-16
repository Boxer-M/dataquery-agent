"""Resume the current hybrid plan using previously verified SQL results."""
import json
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from .metadata_service import fingerprint
from .metric_query_service import MetricQueryService, ROLE
from .rag.routing import catalog_tasks
from .rag.documents import catalog_document


def sql_checkpoint(service, plan, routes, *, capture_snapshot=False):
    """Prove source contents and effective read access without executing a metric."""
    requests = [call["request"] for call in plan["required_tool_calls"]]
    sources = {}
    for route in routes:
        for name, columns in route["required_fields"].items():
            sources.setdefault(name, set()).update(columns)
    try:
        settings = conninfo_to_dict(service.dsn or "")
        if (settings.get("user"), settings.get("dbname")) != (ROLE, "sqlshed") or not sources:
            return None
        current = MetricQueryService(None)
        for request in requests:
            service.validate(request)
            current.validate(request)
        mids = plan["metric_ids"]
        def contracts(item):
            return fingerprint([item.catalog["catalog_version"], item.cutoff.isoformat(),
                                [(mid, item.metrics[mid], item.registry[mid], item.templates[mid]) for mid in mids]])

        contract = contracts(service)
        if contract != contracts(current):
            return None
        with psycopg.connect(service.dsn, connect_timeout=5) as pg:
            pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            pg.execute("SET LOCAL search_path = pg_catalog")
            pg.execute("SET LOCAL statement_timeout = '5s'")
            identity = pg.execute("""SELECT session_user, current_user, current_database(),
                                            inet_server_addr()::text, inet_server_port(),
                                            pg_postmaster_start_time()::text,
                                            (SELECT oid FROM pg_database WHERE datname=current_database())""").fetchone()
            if identity[:3] != (ROLE, ROLE, "sqlshed"):
                return None
            snapshot = pg.execute("SELECT pg_current_snapshot()::text").fetchone()[0]
            # Physical warehouse tables only. RLS or a view requires a different proof.
            hashes = {}
            for name, columns in sorted(sources.items()):
                meta = pg.execute("""SELECT oid, pg_relation_filenode(oid), relkind, relrowsecurity, relhassubclass
                                     FROM pg_class WHERE oid=to_regclass(%s)""", (name,)).fetchone()
                if not meta or meta[2:] != ("r", False, False):
                    return None
                digest = sha256()
                with pg.cursor(name="recovery_fingerprint") as cursor:
                    cursor.execute(sql.SQL("SELECT jsonb_build_array({})::text FROM {} ORDER BY 1").format(
                        sql.SQL(", ").join(map(sql.Identifier, sorted(columns))), sql.Identifier(*name.split("."))))
                    for (row,) in cursor:
                        digest.update((row + "\n").encode())
                hashes[name] = [*meta[:2], sorted(columns), digest.hexdigest()]
        return {"contract": contract, "plan": fingerprint(plan),
                "data": fingerprint([identity, hashes]), "sources": sorted(sources),
                **({"snapshot": snapshot} if capture_snapshot else {})}
    except (psycopg.Error, ValueError, KeyError, OSError):
        return None


def checkpoint_partial(checkpoint, *, results, sql_answer, metric_results=None, plan_id, attempt_id):
    payload = {"results": results, "metric_results": metric_results or []}
    if not checkpoint or len(json.dumps(payload, default=str).encode()) > 65536:
        return None
    return {"plan_id": plan_id, "attempt_id": attempt_id, "status": "partial",
                "checkpoint": checkpoint, "sql_answer": sql_answer, **payload}


def recover_catalog(agent, prior, owner, today, answer):
    saved = prior["recovery"]
    plan, tasks = prior["resolved_request"], prior["subtasks"]
    frame = {"routes": answer["routes"], "event_time_hint": None}
    routes = [agent.check_route(call["request"], frame) for call in plan["required_tool_calls"]]
    if (not all(route["status"] == "STANDARD_METRIC" for route in routes)
            or sql_checkpoint(agent.service, plan, routes) != saved["checkpoint"]):
        return None
    missing = [task["metric_id"] for task in tasks if task["kind"] == "catalog" and task["status"] != "ok"]
    if not missing or not agent.knowledge:
        return None
    retained = [task for task in tasks if task["kind"] == "catalog" and task["status"] == "ok"]
    if retained:
        service = agent.service
        docs = [catalog_document(task["metric_id"], service.metrics[task["metric_id"]],
                                 service.catalog["catalog_version"]) for task in retained]
        active = agent.knowledge.retriever.store.catalog_chunks(docs, today=today, owner=owner)
        chunks = {(row["chunk_id"], row["document_version"], row["content_hash"]) for row in active}
        if any(tuple(task["citation"][key] for key in ("chunk_id", "document_version", "content_hash"))
               not in chunks for task in retained):
            return None
    resolution = {"method": "catalog_recovery", "metric_ids": missing}
    knowledge = agent.knowledge.exact(agent.service, resolution, today=today, owner=owner)
    answer["trace"]["tools"].append(knowledge["retrieval_trace"])
    replacements = {task["metric_id"]: task for task in catalog_tasks(knowledge, missing)}
    attempt_id = uuid4().hex
    updated = [
        dict(task | replacements[task["metric_id"]], attempt_id=attempt_id)
        if task["kind"] == "catalog" and task["metric_id"] in replacements else task
        for task in tasks
    ]
    if sql_checkpoint(agent.service, plan, routes) != saved["checkpoint"]:
        return None
    citations = [task["citation"] for task in updated if task["kind"] == "catalog" and task["status"] == "ok"]
    complete = all(task["status"] == "ok" for task in updated)
    knowledge["citations"] = citations
    knowledge["evidence_status"] = "supported" if complete else "partial" if citations else "insufficient"
    older_evidence = [task["citation"] for task in retained]
    definitions = "\n".join(f"{item['quote']}（{item['source_uri']} · {item['document_version']}）"
                            for item in older_evidence)
    result = {"path": "hybrid", "plan": plan, "plan_id": saved["plan_id"], "attempt_id": attempt_id, "results": saved["results"], "metric_results": saved["metric_results"], "subtasks": updated,
              "metric_resolution": resolution, "knowledge_result": knowledge,
              "recovery": dict(saved, attempt_id=attempt_id, previous_query_id=prior["query_id"],
                               status="completed" if complete else "partial")}
    answer.update(task="hybrid", status="ok" if complete else "partial", result=result,
                  answer_zh="\n".join(filter(None, [saved["sql_answer"], "正式定义", definitions, knowledge["answer_zh"]])))
    return answer
