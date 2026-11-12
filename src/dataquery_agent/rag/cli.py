"""Developer CLI for indexing, inspection and knowledge retrieval."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from ..conversation_manager import local_manager
from ..metric_query_service import MetricQueryService
from ..metrics import ROOT
from .answer import KnowledgeService
from .documents import KnowledgeError, REPOSITORY_SOURCES, catalog_documents, parse_file
from .embedding import GeminiEmbedding
from .retrieval import Retriever
from .routing import RETURN_METRICS
from .store import KnowledgeStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--all-metrics", action="store_true")
    seed.add_argument("--technical-version")
    seed.add_argument("--allow-external", action="store_true")
    ingest = sub.add_parser("index")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--source", required=True)
    ingest.add_argument("--version", required=True)
    ingest.add_argument("--topic")
    ingest.add_argument("--effective")
    ingest.add_argument("--expires")
    ingest.add_argument("--type", choices=("technical", "business_rule", "synthetic_fixture"), required=True)
    ingest.add_argument("--allow-external", action="store_true", help="Confirm content may be sent to Gemini.")
    for command in ("list", "chunks", "delete"):
        action = sub.add_parser(command)
        action.add_argument("--public", action="store_true")
        if command != "list":
            action.add_argument("document_id")
    for command in ("search", "ask"):
        action = sub.add_parser(command)
        action.add_argument("question")
        action.add_argument("--synthetic", action="store_true")
        action.add_argument("--public", action="store_true")
    args = parser.parse_args()
    owner = None if getattr(args, "public", False) else dotenv_values(ROOT / ".env.agent-meta").get("AGENT_OWNER_TOKEN")
    embedding = GeminiEmbedding.local()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        if args.command in {"search", "ask"}:
            if args.command == "search":
                result = Retriever(KnowledgeStore.local(), embedding).search(
                    args.question, today=today, owner=owner, synthetic=args.synthetic)
            else:
                manager, _ = local_manager()
                manager.agent.knowledge = KnowledgeService.local(manager.agent.service.metrics, synthetic=args.synthetic)
                result = manager.agent.answer(args.question, manager.generate, knowledge_owner=owner)
        else:
            store = KnowledgeStore.local(admin=True)
            if args.command in {"seed", "index"} and not args.allow_external:
                raise KnowledgeError("external_embedding_confirmation_required")
            if args.command == "seed":
                metrics = MetricQueryService(None)
                result = [store.index(doc, embedding) for doc in catalog_documents(metrics)
                          if args.all_metrics or doc.topic in RETURN_METRICS]
                if args.technical_version:
                    for source in sorted(REPOSITORY_SOURCES):
                        doc = parse_file(ROOT / source, title=Path(source).stem, source_type="technical",
                                         source_uri=source, version=args.technical_version)
                        result.append(store.index(doc, embedding))
            elif args.command == "index":
                doc = parse_file(args.path, title=args.title, source_type=args.type, source_uri=args.source,
                                 version=args.version, topic=args.topic, effective=args.effective, expires=args.expires)
                result = store.index(doc, embedding, owner=None if args.type == "technical" else owner,
                                     external_approved=args.allow_external)
            elif args.command == "list":
                result = store.list_versions(owner=owner)
            elif args.command == "chunks":
                result = store.chunks(args.document_id, owner=owner)
            else:
                result = {"deleted": store.delete(args.document_id, owner=owner)}
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, KnowledgeError) else type(error).__name__
        print(json.dumps({"status": "error", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
