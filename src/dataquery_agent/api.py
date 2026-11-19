"""Loopback-only development HTTP API over the existing Conversation Manager."""
from asyncio import timeout
from hashlib import sha256
from hmac import compare_digest
import json
import re
from time import perf_counter_ns
from uuid import UUID

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api_models import (
    ApiError, ChatRequest, ConversationResponse, ConversationPage, MessagePage,
    HealthResponse, QueryResponse, ResumeRequest, public_response, text,
)
from .conversation_manager import local_manager
from .conversation_store import ConversationError
from .artifact_api import artifact_router
from .artifact_store import ArtifactStore
from .document_api import DOCUMENTS, body_limit, document_router
from .document_staging import DocumentStaging
from .metrics import ROOT
from .performance import Performance, WorkBusy, timed

MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 256 * 1024


def failure(status, code, message):
    return JSONResponse({"error": ApiError(code=code, message=message).model_dump()}, status_code=status)


class LocalRequestLimits:
    def __init__(self, app):
        self.app = app
        self.document_write = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not scope.get("client") or scope["client"][0] != "127.0.0.1":
            return await failure(403, "local_only", "此服务仅接受本机请求。")(scope, receive, send)
        if scope["method"] != "POST":
            return await self.app(scope, receive, send)
        is_document = scope["path"] == DOCUMENTS or scope["path"].startswith(DOCUMENTS + "/")
        if is_document and self.document_write:
            return await failure(409, "document_operation_busy", "文档操作正在处理，请重试。")(scope, receive, send)
        if is_document:
            self.document_write = True
        try:
            await self.bounded_request(scope, receive, send)
        finally:
            if is_document:
                self.document_write = False

    async def bounded_request(self, scope, receive, send):
        # Read a bounded body before framework parsing. Also covers chunked requests.
        limit = body_limit(scope["path"], MAX_REQUEST_BYTES)
        body = bytearray()
        try:
            async with timeout(15):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    chunk = event.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        return await failure(413, "request_too_large", "请求体过大。")(scope, receive, send)
                    body.extend(chunk)
                    if not event.get("more_body"):
                        break
        except TimeoutError:
            return await failure(408, "request_timeout", "请求体接收超时。")(scope, receive, send)
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def bounded(model):
    payload = model.model_dump_json(exclude_none=True).encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        return failure(413, "response_too_large", "响应超出上限，请缩小查询范围。")
    return Response(payload, media_type="application/json", headers={"Cache-Control": "no-store"})


def create_app(*, token, manager_factory=local_manager, document_staging=None, performance=None, artifact_store=None):
    if not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", token):
        raise ValueError("Configure DATAQUERY_HTTP_TOKEN as 64 hexadecimal characters.")
    app = FastAPI(title="DataQuery Agent API", version="1.0", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_middleware(LocalRequestLimits)
    bearer = HTTPBearer(auto_error=False)
    workload = performance if performance is not None else Performance.local()

    def authorize(auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if auth is None or not compare_digest(auth.credentials.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
    def identity(_=Depends(authorize)):
        return manager_factory()

    def document_owner(_=Depends(authorize)):
        # Reuse the same trusted owner as chat and history.
        return manager_factory()[1]

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        messages = {401: ("unauthorized", "需要有效的本地访问令牌。"),
                    404: ("not_found", "没有可访问的记录。")}
        code, message = messages.get(error.status_code, ("request_failed", "请求未能完成。"))
        response = failure(error.status_code, code, message)
        response.headers.update(error.headers or {})
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return failure(422, "invalid_request", "请求字段或格式无效。")

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _error):
        return failure(500, "service_error", "服务暂时无法处理请求。")

    @app.get("/api/health", response_model=HealthResponse)
    def health():
        return HealthResponse()

    @app.post("/api/conversations", response_model=ConversationResponse, status_code=201)
    def create_conversation(context=Depends(identity)):
        manager, owner = context
        return ConversationResponse(conversation_id=manager.create_conversation(owner)["conversation_id"])

    @app.get("/api/conversations", response_model=ConversationPage)
    def conversations(offset: int = Query(0, ge=0, le=9999), context=Depends(identity)):
        manager, owner = context
        rows = manager.store.list_conversations(owner, offset)
        return ConversationPage(conversations=[dict(row, title=text(row["title"], 80) or "新会话")
                                               for row in rows[:20]], has_more=len(rows) > 20)

    @app.get("/api/conversations/{conversation_id}/messages", response_model=MessagePage)
    def messages(conversation_id: UUID, offset: int = Query(0, ge=0, le=9999), context=Depends(identity)):
        manager, owner = context
        try:
            rows = manager.store.list_messages(owner, conversation_id, offset)
        except ConversationError as error:
            if error.code == "conversation_not_found":
                raise HTTPException(404) from None
            raise
        return MessagePage(messages=[dict(row, question=text(row["question"], 500) or "已保存查询")
                                     for row in rows[:20]], has_more=len(rows) > 20)

    def throttled(retry_after, code="rate_limited"):
        result = failure(429, code, "请求过多，请稍后重试。")
        result.headers["Retry-After"] = str(retry_after)
        return result

    def run_work(action):
        try:
            with workload.slot("http"):
                started = perf_counter_ns() if workload.report else 0
                manager, owner = manager_factory()
                identity_key = sha256(owner.encode()).hexdigest()
                with workload.scope(identity_key) as work:
                    if workload.report:
                        work.durations["setup"] = perf_counter_ns() - started
                    if retry_after := workload.admit(identity_key):
                        result = throttled(retry_after)
                    else:
                        result = action(manager, owner)
                if workload.report:
                    result.headers.update(work.headers())
                return result
        except WorkBusy:
            return throttled(1, "request_busy")

    @app.post("/api/chat", response_model=QueryResponse)
    def chat(body: ChatRequest, _=Depends(authorize)):
        def action(manager, owner):
            with timed("route"):
                data = manager.process_message(owner, body.conversation_id, body.question, body.message_id)
            if data.get("code") == "conversation_not_found":
                raise HTTPException(404)
            with timed("assembly"):
                return bounded(public_response(data))

        return run_work(action)

    @app.post("/api/queries/{query_id}/resume", response_model=QueryResponse)
    def resume_query(query_id: UUID, body: ResumeRequest, _=Depends(authorize)):
        from .hitl import update_resume

        def action(manager, owner):
            try:
                data = update_resume(manager, owner, query_id, body.metric_id)
            except ConversationError as error:
                if error.code == "query_not_found":
                    raise HTTPException(404) from None
                return failure(409, error.code, "该澄清已过期、正在处理，或所选口径无效。")
            return bounded(public_response(data))

        return run_work(action)

    @app.get("/api/queries/{query_id}", response_model=QueryResponse)
    def history(query_id: UUID, context=Depends(identity)):
        manager, owner = context
        try:
            data = manager.get_query(owner, query_id)
        except ConversationError as error:
            if error.code == "query_not_found":
                raise HTTPException(404) from None
            raise
        return bounded(public_response(data, history=True))

    def document_response(data, status):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if len(payload) > 8 * 1024 * 1024:
            return failure(413, "response_too_large", "文档预览超出响应上限。")
        return Response(payload, status_code=status, media_type="application/json", headers={"Cache-Control": "no-store"})

    app.include_router(document_router(document_owner, staging=document_staging or DocumentStaging(),
                                       respond=document_response, failure=failure))
    app.include_router(artifact_router(identity, store=artifact_store or ArtifactStore(), failure=failure))
    return app


def main():
    import uvicorn

    config = dotenv_values(ROOT / ".env.api")
    try:
        app = create_app(token=config.get("DATAQUERY_HTTP_TOKEN"))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    uvicorn.run(app, host="127.0.0.1", port=8000, proxy_headers=False, ws="none", access_log=False)


if __name__ == "__main__":
    main()
