from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/tg/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    container = request.app.state.container
    expected = container.config.telegram_webhook_secret
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(got.encode(), expected.encode()):
        return JSONResponse({"error": {"code": "forbidden", "message": "Доступ запрещён"}}, status_code=403)
    if not container.dispatcher.accepting:
        return JSONResponse({"error": {"code": "shutting_down", "message": "Сервис перезапускается"}}, 503)
    try:
        raw: Any = await request.json()
    except ValueError:
        return JSONResponse({"ok": True})
    if isinstance(raw, dict) and isinstance(raw.get("update_id"), int):
        container.dispatcher.spawn(raw)
    return JSONResponse({"ok": True})
