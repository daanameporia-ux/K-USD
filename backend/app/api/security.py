from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://telegram.org; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
)

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP,
}


MAX_BODY = 64 * 1024  # bytes; the largest legitimate JSON request is a few KB
UPLOAD_MAX_BODY = 10 * 1024 * 1024  # the payment screenshot (same as domain.buyer.PROOF_MAX_BYTES)
_UPLOAD_PATH = re.compile(r"/api/client/orders/\d+/proof\Z")


class BodyLimitMiddleware:
    """Reject request bodies above MAX_BODY before they are read into memory (pure ASGI)."""

    def __init__(self, app: ASGIApp, max_body: int = MAX_BODY) -> None:
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        max_body = self.max_body
        if scope.get("method") == "POST" and _UPLOAD_PATH.match(scope.get("path") or ""):
            max_body = max(max_body, UPLOAD_MAX_BODY)
        if declared is not None and (not declared.isdigit() or int(declared) > max_body):
            await _too_large(send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_body:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _too_large(send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send: Send) -> None:
    body = json.dumps({"error": {"code": "too_large", "message": "Слишком большой запрос"}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
            + [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()],
        }
    )
    await send({"type": "http.response.body", "body": body})


def install(app: FastAPI) -> None:
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def _security(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            response = await call_next(request)
        for key, value in HEADERS.items():
            response.headers.setdefault(key, value)
        response.headers["X-Request-Id"] = request_id
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
