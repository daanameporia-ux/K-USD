"""Uniform JSON errors: {"error": {"code": "...", "message": "..."}} with Russian messages."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.domain.errors import ServiceError
from app.logging import get_logger

log = get_logger(__name__)

HTTP_MESSAGES = {
    400: ("bad_request", "Некорректный запрос"),
    401: ("unauthorized", "Требуется авторизация"),
    403: ("forbidden", "Недостаточно прав"),
    404: ("not_found", "Не найдено"),
    405: ("method_not_allowed", "Метод не поддерживается"),
    413: ("too_large", "Слишком большой запрос"),
    429: ("rate_limited", "Слишком много запросов"),
}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {"error": body}


def install(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in e.get("loc", ()) if p != "body") for e in exc.errors()]
        return JSONResponse(
            error_body("validation_error", "Некорректные данные запроса", {"fields": fields}), status_code=400
        )

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        code, message = HTTP_MESSAGES.get(exc.status_code, ("error", "Ошибка"))
        return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(error_body("internal_error", "Внутренняя ошибка, попробуйте позже"), status_code=500)
