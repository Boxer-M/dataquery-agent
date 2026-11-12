"""Governance commands for explicitly authorized local developers."""
import argparse
import json
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from .conversation_store import ConversationError, ConversationStore
from .dynamic_sql_engine import DynamicSqlEngine
from .metrics import ROOT
from .query_asset_governance import QueryAssetGovernance
from .query_asset_store import QueryAssetStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("from-history").add_argument("query_id")
    sub.add_parser("propose").add_argument("sql_file", type=Path)
    detail = sub.add_parser("from-detail")
    detail.add_argument("analysis")
    detail.add_argument("groups", nargs="*")
    for command in ("show", "validate", "publish", "impact", "retire", "reject"):
        action = sub.add_parser(command)
        action.add_argument("asset_id")
        action.add_argument("version", type=int)
        if command == "impact":
            action.add_argument("--mark", action="store_true")
    args = parser.parse_args()
    try:
        store = QueryAssetStore.local(admin=True)
        service = QueryAssetGovernance(store, DynamicSqlEngine.local())
        if args.command == "from-history":
            cfg = dotenv_values(ROOT / ".env.agent-meta")
            result = service.from_history(ConversationStore(cfg.get("AGENT_META_DSN")), cfg.get("AGENT_OWNER_TOKEN"),
                                          args.query_id)
        elif args.command == "from-detail":
            result = service.from_detail(args.analysis, args.groups)
        elif args.command == "propose":
            result = service.propose(args.sql_file.read_text(), {"type": "developer_sql"})
        else:
            ids = (args.asset_id, args.version)
            if args.command == "impact":
                result = service.impact(*ids, mark=args.mark)
            elif args.command in {"retire", "reject"}:
                result = store.transition(*ids, {"retire": "RETIRED", "reject": "REJECTED"}[args.command])
            else:
                result = {"show": store.read, "validate": service.verify, "publish": service.publish}[args.command](*ids)
    except (ConversationError, ValueError, OSError, KeyError, psycopg.Error) as error:
        from .query_asset_store import AssetError
        code = str(error) if isinstance(error, AssetError) else getattr(error, "code", "asset_operation_failed")
        print(json.dumps({"status": "error", "code": code}))
        return 1
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 1 if args.command == "validate" and result["lifecycle_status"] != "VALIDATED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
