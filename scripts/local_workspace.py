"""Check and start the local API and workbench with Ctrl-C cleanup."""
import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    ".env": ("POSTGRES_USER", "POSTGRES_PASSWORD", "GEMINI_API_KEY", "GEMINI_MODEL"),
    ".env.agent-meta": ("AGENT_META_DSN", "AGENT_OWNER_TOKEN"),
    ".env.metric-query": ("METRIC_QUERY_DSN",),
    ".env.detail-query": ("DETAIL_QUERY_DSN",),
    ".env.dynamic-query": ("DYNAMIC_QUERY_DSN",),
    ".env.query-assets": ("QUERY_ASSET_DSN",),
    ".env.knowledge": ("KNOWLEDGE_DSN",),
    ".env.knowledge-admin": ("KNOWLEDGE_DSN",),
}
LOCAL = "127.0.0.1"


def preflight():
    if not shutil.which("npm") or not (ROOT / "frontend/node_modules/vite/package.json").exists():
        raise SystemExit("Install Node and run npm --prefix frontend ci first.")
    for filename, keys in CONFIG.items():
        values = dotenv_values(ROOT / filename)
        if missing := [key for key in keys if not values.get(key)]:
            raise SystemExit(f"{filename}: missing {', '.join(missing)}")
    token = dotenv_values(ROOT / ".env.api").get("DATAQUERY_HTTP_TOKEN") or ""
    if not re.fullmatch(r"[a-fA-F0-9]{64}", token):
        raise SystemExit(".env.api: configure DATAQUERY_HTTP_TOKEN.")
    try:
        with socket.create_connection((LOCAL, 5433), timeout=2):
            pass
    except OSError:
        raise SystemExit("PostgreSQL is not listening on 127.0.0.1:5433.") from None
    print("Local configuration and PostgreSQL listener available.", flush=True)


def workspace():
    for port in (8000, 5173):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((LOCAL, port))
            except OSError:
                raise SystemExit(f"Port {port} is occupied; stop its current service first.") from None
    commands = ([sys.executable, "-m", "dataquery_agent.api"], ["npm", "--prefix", "frontend", "run", "dev"])
    children = []
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, start_new_session=True,
                                             env=os.environ | {"PYTHONPATH": str(ROOT / "src")}))
        health = Request("http://127.0.0.1:5173/api/health", headers={"X-DataQuery-Client": "workspace"})
        http = build_opener(ProxyHandler({}))
        deadline = monotonic() + 20
        while monotonic() < deadline and all(child.poll() is None for child in children):
            try:
                with http.open(health, timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError):
                sleep(.2)
        else:
            raise SystemExit("Workspace health check failed; inspect local service output.")
        print("Open http://127.0.0.1:5173/  (Ctrl-C stops API and Vite)", flush=True)
        while all(child.poll() is None for child in children):
            sleep(.25)
        raise SystemExit("A workspace service exited.")
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check local config without starting services")
    args = parser.parse_args()
    preflight()
    if not args.check:
        workspace()
