"""Run the existing Chrome suite against the real API with an isolated PDF fixture."""
from contextlib import ExitStack
import json
import os
from secrets import token_hex
import socket
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

from dotenv import dotenv_values
import uvicorn

from dataquery_agent.api import create_app
from dataquery_agent.conversation_manager import local_manager
from dataquery_agent.document_staging import DocumentStaging
from dataquery_agent.metrics import ROOT
from dataquery_agent.rag.pdf_cli import MeasuredEmbedding
from dataquery_agent.rag.store import KnowledgeStore
from evaluate_integrated_e2e import stage_pdf
from test_document_api import native_sample, PDF


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
        if (os.getenv("WORKSPACE_SQL_DOCUMENT_E2E") and
                result.get("result", {}).get("path") == "sql_document_hybrid" and result["status"] == "ok"):
            writer.delete(doc_id, owner=owner)
        attempts.append({"query_id": result.get("query_id"), "replayed": result.get("replayed", False),
                         "model_attempts": result.get("trace", {}).get("model_attempts", [])})
        return result

    manager.process_message = traced
    with ExitStack() as cleanup:
        ui_enabled = os.getenv("WORKSPACE_DOCUMENT_UI_E2E") == "1"
        staging = None
        if ui_enabled:
            scratch = Path(cleanup.enter_context(TemporaryDirectory()))
            native = scratch / "native-sample.pdf"
            native.write_bytes(native_sample())
            stage_engine = MeasuredEmbedding.local()
            real_embed = stage_engine.embed
            failures = 0

            def fail_once(texts, *, query=False):
                nonlocal failures
                if not failures:
                    failures += 1
                    raise TimeoutError("Controlled index timeout")
                return real_embed(texts, query=query)

            stage_engine.embed = fail_once
            staging = DocumentStaging(scratch / "staging", publisher=writer, embedding=stage_engine)

            def withdraw_ui_documents():
                for row in writer.list_versions(owner=owner):
                    if row.get("source_uri", "").startswith("reviewed-pdf:api-"):
                        writer.delete(row["document_id"], owner=owner)

            cleanup.callback(withdraw_ui_documents)

        api = create_app(token=dotenv_values(ROOT / ".env.api")["DATAQUERY_HTTP_TOKEN"],
                         manager_factory=lambda: (manager, owner), document_staging=staging)
        server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=8000, proxy_headers=False,
                                                ws="none", access_log=False))
        doc_id, _ = stage_pdf(writer, engine, owner, cleanup)
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(.05)
            if not server.started:
                raise RuntimeError("Local evaluation API failed to start")
            document_ui = None
            if ui_enabled:
                document_ui = json.loads(subprocess.check_output(
                    ["node", "tests/document-browser.mjs"], cwd=ROOT / "frontend",
                    env=os.environ | {"WORKSPACE_NATIVE_PDF": str(native), "WORKSPACE_SCAN_PDF": str(PDF)}, text=True,
                ))
                document_ui.update(index_batches=stage_engine.batches, injected_timeouts=failures)
                if path := os.getenv("WORKSPACE_DOCUMENT_UI_REPORT"):
                    Path(path).write_text(json.dumps(document_ui, ensure_ascii=False, indent=2))
            response = subprocess.check_output(["node", "tests/browser.mjs"], cwd=ROOT / "frontend",
                                               env=os.environ | {"WORKSPACE_PDF_E2E": "1"}, text=True)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
    result = json.loads(response)
    return {"browser": result, "document_ui": document_ui, "embedding_batches": engine.batches, "api_requests": attempts,
            "fixture_deactivated": not any(not row["deleted_at"]
                                          for row in writer.list_versions(owner=owner))}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, default=str, indent=2))
