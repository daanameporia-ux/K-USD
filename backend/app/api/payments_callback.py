from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services import orders

router = APIRouter()

_MESSAGES = {
    200: ("ok", "OK"),
    400: ("bad_request", "Некорректный callback"),
    401: ("bad_signature", "Неверная подпись"),
    404: ("unknown_provider", "Неизвестный провайдер"),
    500: ("internal_error", "Внутренняя ошибка, повторите позже"),
}


@router.post("/api/payments/callback/{provider}")
async def payment_callback(provider: str, request: Request) -> JSONResponse:
    body = await request.body()
    result = await orders.handle_callback(request.app.state.container, provider, dict(request.headers), body)
    if result.status == 200:
        return JSONResponse({"ok": True, "result": result.result})
    code, message = _MESSAGES.get(result.status, ("error", "Ошибка"))
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=result.status)
