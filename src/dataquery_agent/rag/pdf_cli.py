"""Review, publish and retrieve owner-scoped PDF evidence."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from secrets import token_hex
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from ..document_ingestion import ExtractionError
from ..metrics import ROOT
from .documents import KnowledgeError
from .embedding import GeminiEmbedding
from .pdf_review import KEY_PATH, approve, read_json
from .retrieval import Retriever
from .store import KnowledgeStore


class MeasuredEmbedding(GeminiEmbedding):
    def __init__(self, api_key):
        super().__init__(api_key)
        self.batches = []

    def record_batch(self, response, *, query, elapsed_ms):
        self.batches.append({
            "task_type": "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
            "elapsed_ms": elapsed_ms,
            "token_counts": [getattr(getattr(item, "statistics", None), "token_count", None)
                             for item in response.embeddings],
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-key")
    review = commands.add_parser("review")
    review.add_argument("extraction", type=Path)
    review.add_argument("pdf", type=Path)
    review.add_argument("output", type=Path)
    for option in ("slug", "title", "basis"):
        review.add_argument(f"--{option}", required=True)
    review.add_argument("--corrections", type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("receipt", type=Path)
    publish.add_argument("pdf", type=Path)
    search = commands.add_parser("search")
    search.add_argument("question")
    for command in (publish, search):
        command.add_argument("--allow-external", action="store_true")
    args = parser.parse_args()
    try:
        owner = dotenv_values(ROOT / ".env.agent-meta").get("AGENT_OWNER_TOKEN")
        if args.command == "init-key":
            with os.fdopen(os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                stream.write(token_hex(32) + "\n")
            result = {"status": "review_key_created"}
        elif args.command == "review":
            receipt = approve(read_json(args.extraction), args.pdf, owner=owner, slug=args.slug,
                              title=args.title, basis=args.basis,
                              corrections=read_json(args.corrections) if args.corrections else {})
            with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                json.dump(receipt, stream, ensure_ascii=False, indent=2)
            result = {"status": "reviewed", "pages": len(receipt["review"]["pages"])}
        else:
            if not args.allow_external:
                raise KnowledgeError("external_embedding_confirmation_required")
            engine = MeasuredEmbedding.local()
            if args.command == "publish":
                result = KnowledgeStore.local(admin=True).index(
                    None, engine, owner=owner, external_approved=True,
                    pdf_receipt=read_json(args.receipt), pdf=args.pdf)
            else:
                rows, trace = Retriever(KnowledgeStore.local(), engine).search(
                    args.question, today=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                    owner=owner, reviewed_pdf=True)
                result = {"rows": rows, "trace_id": trace["trace_id"]}
            result["embedding_batches"] = engine.batches
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, (KnowledgeError, ExtractionError)) else type(error).__name__
        print(json.dumps({"status": "error", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
