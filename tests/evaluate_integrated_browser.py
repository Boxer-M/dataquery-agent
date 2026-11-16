"""Run the existing Chrome suite against the real API with an isolated PDF fixture."""
from contextlib import ExitStack
import json
import os
from secrets import token_hex
import socket
import subprocess
from threading import Thread
from time import monotonic, sleep

from dotenv import dotenv_values
import uvicorn

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_integrated_e2e import stage_pdf


def run():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 8000))
    owner = token_hex(32)
    manager, _ = local_manager()
    engine = MeasuredEmbedding.local()
    manager.agent.knowledge.retriever.embedding = engine
    writer = KnowledgeStore.local(admin=True)
    attempts = []
    process = manager.process_message

    def traced(*args, **kwargs):
        result = process(*args, **kwargs)
        attempts.append({"query_id": result.get("query_id"), "replayed": result.get("replayed", False),
                         "model_attempts": result.get("trace", {}).get("model_attempts", [])})
        return result

    manager.process_message = traced
    api = create_app(token=dotenv_values(ROOT / ".env.api")["DATAQUERY_HTTP_TOKEN"],
                     manager_factory=lambda: (manager, owner))
    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=8000, proxy_headers=False,
                                            ws="none", access_log=False))
    with ExitStack() as cleanup:
        doc_id, _ = stage_pdf(writer, engine, owner, cleanup)
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(.05)
            if not server.started:
                raise RuntimeError("Local evaluation API failed to start")
            response = subprocess.check_output(["node", "tests/browser.mjs"], cwd=ROOT / "frontend",
                                               env=os.environ | {"WORKSPACE_PDF_E2E": "1"}, text=True)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
    result = json.loads(response)
    return {"browser": result, "embedding_batches": engine.batches, "api_requests": attempts,
            "fixture_deactivated": not any(row["document_id"] == doc_id and not row["deleted_at"]
                                          for row in writer.list_versions(owner=owner))}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, default=str, indent=2))
