"""Owner-scoped PDF import, review and explicit publication endpoints."""
from collections.abc import Callable
import re
import sqlite3
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints
from starlette.concurrency import run_in_threadpool

from .document_ingestion import ExtractionError, MAX_BYTES
from .document_staging import DocumentStaging, StageError
from .rag.documents import KnowledgeError

DOCUMENTS = "/api/documents"
DOCUMENT_ID_PATH = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
REVIEW_PATH = re.compile(rf"{DOCUMENTS}/{DOCUMENT_ID_PATH}/review")
VERSION_PATH = re.compile(rf"{DOCUMENTS}/{DOCUMENT_ID_PATH}/versions")
MAX_REVIEW_BYTES = 512 * 1024
PDF_BODY = {"requestBody": {"required": True, "content": {
    "application/pdf": {"schema": {"type": "string", "format": "binary"}},
}}}


def body_limit(path, default):
    if path == DOCUMENTS or VERSION_PATH.fullmatch(path):
        return MAX_BYTES
    return MAX_REVIEW_BYTES if REVIEW_PATH.fullmatch(path) else default


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: UUID
    title: str = Field(min_length=1, max_length=150)
    basis: str = Field(min_length=1, max_length=1000)
    corrections: dict[Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]?$")],
                      Annotated[str, StringConstraints(min_length=1, max_length=100_000)]] = Field(
                          default_factory=dict, max_length=40)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: UUID
    review_version: Annotated[str, StringConstraints(pattern=r"^pdf-[0-9a-f]{48}$")]
    external_embedding_approved: StrictBool


def document_router(identity, *, staging: DocumentStaging, respond: Callable, failure: Callable):
    router = APIRouter(prefix=DOCUMENTS)

    def operation(action, *, status=200, publishing=False):
        try:
            return respond(action(), status)
        except StageError as error:
            return failure(error.status, str(error), "无法处理该文档或版本。")
        except ExtractionError as error:
            code = str(error)
            if code == "install_requirements_pdf":
                return failure(503, code, "PDF 提取组件尚未就绪。")
            return failure(422, code, "PDF 无法在当前提取限制内处理。")
        except KnowledgeError as error:
            code = str(error)
            if code.startswith(("review_key_", "invalid_review_key")):
                return failure(503, "review_signer_unavailable", "本地审核签名服务不可用。")
            return failure(409 if publishing else 422, code, "文档审核或发布校验未通过。")
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", 0)
            if code & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return failure(409, "document_operation_busy", "文档操作正在处理，请重试。")
            raise
        except (OSError, psycopg.Error):
            return failure(503, "document_service_unavailable", "本地文档服务暂不可用。")
        except Exception:
            if publishing:
                return failure(503, "document_publish_unavailable", "索引服务暂不可用。")
            raise

    async def upload(request, owner, document_id=None):
        kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind != "application/pdf":
            return failure(415, "pdf_content_type_required", "请提交 application/pdf 请求体。")
        raw = await request.body()
        return await run_in_threadpool(operation, lambda: staging.import_pdf(owner, raw, document_id), status=201)

    @router.post("", status_code=201, openapi_extra=PDF_BODY)
    async def create(request: Request, owner=Depends(identity)):
        return await upload(request, owner)

    @router.post("/{document_id}/versions", status_code=201, openapi_extra=PDF_BODY)
    async def replace(document_id: UUID, request: Request, owner=Depends(identity)):
        return await upload(request, owner, str(document_id))

    @router.get("/{document_id}")
    def preview(document_id: UUID, owner=Depends(identity)):
        return operation(lambda: staging.get(owner, str(document_id)))

    @router.get("/{document_id}/pages/{page}")
    def preview_page(document_id: UUID, page: int, owner=Depends(identity)):
        return operation(lambda: staging.get(owner, str(document_id), page))

    @router.post("/{document_id}/review")
    def review(document_id: UUID, body: ReviewRequest, owner=Depends(identity)):
        return operation(lambda: staging.review(owner, str(document_id), str(body.version),
                                                title=body.title, basis=body.basis, corrections=body.corrections))

    @router.post("/{document_id}/publish")
    def publish(document_id: UUID, body: PublishRequest, owner=Depends(identity)):
        if not body.external_embedding_approved:
            return failure(422, "external_embedding_confirmation_required", "发布需明确授权发送审核后的文本。")
        return operation(lambda: staging.publish(owner, str(document_id), str(body.version), body.review_version),
                         publishing=True)

    return router
