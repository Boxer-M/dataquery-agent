"""Loopback-only development HTTP API over the existing Conversation Manager."""
from hmac import compare_digest
import re
from uuid import UUID

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api_models import (
    ApiError, ChatRequest, ConversationResponse, HealthResponse, QueryResponse, public_response,
)
from .conversation_manager import local_manager
from .conversation_store import ConversationError
from .metrics import ROOT

MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 256 * 1024


def failure(status, code, message):
    return JSONResponse({"error": ApiError(code=code, message=message).model_dump()}, status_code=status)


class LocalRequestLimits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not scope.get("client") or scope["client"][0] != "127.0.0.1":
            return await failure(403, "local_only", "此服务仅接受本机请求。")(scope, receive, send)
        if scope["method"] != "POST":
            return await self.app(scope, receive, send)
        # Read a bounded body before framework JSON parsing. Also covers chunked requests.
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                return await failure(413, "request_too_large", "请求体过大。")(scope, receive, send)
            if not event.get("more_body"):
                break
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


def create_app(*, token, manager_factory=local_manager):
    if not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", token):
        raise ValueError("Configure DATAQUERY_HTTP_TOKEN as 64 hexadecimal characters.")
    app = FastAPI(title="DataQuery Agent API", version="1.0", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_middleware(LocalRequestLimits)
    bearer = HTTPBearer(auto_error=False)

    def identity(auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if auth is None or not compare_digest(auth.credentials.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
        manager, owner = manager_factory()
        return manager, owner

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

    @app.post("/api/chat", response_model=QueryResponse)
    def chat(body: ChatRequest, context=Depends(identity)):
        manager, owner = context
        data = manager.process_message(owner, body.conversation_id, body.question, body.message_id)
        if data.get("code") == "conversation_not_found":
            raise HTTPException(404)
        return bounded(public_response(data))

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
