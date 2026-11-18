"""Authenticated explicit export/download, without executing business queries."""
import sqlite3
from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from .artifact_store import ArtifactError
from .conversation_store import ConversationError
from .xlsx_report import generate_metric_xlsx

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactMetadata(BaseModel):
    artifact_id: UUID
    query_id: UUID
    filename: str
    created_at: datetime
    sha256: str
    byte_size: int


def artifact_router(identity, *, store, failure):
    router = APIRouter()

    def source(manager, owner, query_id):
        try:
            return manager.get_query(owner, query_id)
        except ConversationError as error:
            if error.code == "query_not_found":
                raise HTTPException(404) from None
            raise

    def operation(action):
        try:
            return action()
        except ArtifactError as error:
            if error.status == 404:
                raise HTTPException(404) from None
            return failure(error.status, str(error), "报表产物暂不可用。")
        except (OSError, sqlite3.Error, psycopg.Error):
            return failure(503, "artifact_storage_unavailable", "报表存储暂不可用。")

    @router.post("/api/queries/{query_id}/artifacts/xlsx", status_code=201, response_model=ArtifactMetadata)
    def export(query_id: UUID, body: ExportRequest, response: Response, context=Depends(identity)):
        response.headers["Cache-Control"] = "no-store"
        manager, owner = context
        def action():
            record = source(manager, owner, query_id)
            snapshot = record.get("export_snapshot")
            if record.get("execution_status") != "ok" or not isinstance(snapshot, dict) or snapshot.get("version") != 1:
                return failure(409, "query_export_unavailable", "该查询未保存可导出的完整汇总结果。")
            try:
                content = generate_metric_xlsx(snapshot["result"], snapshot["metrics"])
            except (ValueError, KeyError, TypeError):
                return failure(409, "query_export_unavailable", "该结果不符合当前报表格式或精度要求。")
            return store.create(owner, query_id, content)
        return operation(action)

    @router.get("/api/artifacts/{artifact_id}/download")
    def download(artifact_id: UUID, context=Depends(identity)):
        manager, owner = context
        def action():
            record = store.get(owner, artifact_id)
            source(manager, owner, record["query_id"])  # Recheck source access, even after retention/pruning.
            return Response(record["content"], media_type=XLSX, headers={
                "Content-Disposition": f'attachment; filename="{record["filename"]}"',
                "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            })
        return operation(action)
    return router
