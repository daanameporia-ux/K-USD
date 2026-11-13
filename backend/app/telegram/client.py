"""Own HTTP client for the Telegram Bot API (no third-party bot frameworks)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from app.domain.text import escape_html
from app.logging import get_logger

__all__ = ["BotBlockedError", "TelegramApiError", "TelegramClient", "TelegramNetworkError", "escape_html"]

log = get_logger(__name__)

CONNECT_TIMEOUT = 5.0
DEFAULT_TIMEOUT = 15.0
POLL_EXTRA = 5.0
MAX_RETRIES = 3


class TelegramApiError(Exception):
    def __init__(self, code: int, description: str, retry_after: int | None = None, method: str = "") -> None:
        super().__init__(f"Telegram API error {code} in {method}: {description}")
        self.code = code
        self.description = description
        self.retry_after = retry_after
        self.method = method


class BotBlockedError(TelegramApiError):
    """403: the user blocked the bot or never started it."""


class TelegramNetworkError(TelegramApiError):
    def __init__(self, method: str, detail: str) -> None:
        super().__init__(0, f"network error: {detail}", None, method)


def _is_not_modified(description: str) -> bool:
    return "message is not modified" in description.lower()


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.telegram.org",
        transport: httpx.AsyncBaseTransport | None = None,
        on_bot_blocked: Callable[[int], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff_base: float = 0.5,
    ) -> None:
        self._base = f"{base_url.rstrip('/')}/bot{token}/"
        self._file_base = f"{base_url.rstrip('/')}/file/bot{token}/"
        self._http = httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=CONNECT_TIMEOUT)
        )
        self.on_bot_blocked = on_bot_blocked
        self._sleep = sleep
        self._backoff = backoff_base

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = self._base + method
        req_timeout = httpx.Timeout(timeout or DEFAULT_TIMEOUT, connect=CONNECT_TIMEOUT)
        attempt = 0
        while True:
            attempt += 1
            try:
                if files:
                    data = {k: (json.dumps(v) if isinstance(v, dict | list) else str(v)) for k, v in params.items()}
                    resp = await self._http.post(url, data=data, files=files, timeout=req_timeout)
                else:
                    resp = await self._http.post(url, json=params, timeout=req_timeout)
            except httpx.HTTPError as exc:
                if attempt > MAX_RETRIES:
                    log.warning("tg_network_error", method=method, error=type(exc).__name__)
                    raise TelegramNetworkError(method, type(exc).__name__) from None
                await self._sleep(self._backoff * 2 ** (attempt - 1))
                continue

            try:
                body = resp.json()
            except ValueError:
                body = {"ok": False, "error_code": resp.status_code, "description": "invalid JSON"}

            if body.get("ok"):
                return body.get("result")

            code = int(body.get("error_code") or resp.status_code)
            description = str(body.get("description") or "")
            retry_after = (body.get("parameters") or {}).get("retry_after")

            if code == 429 and attempt <= MAX_RETRIES:
                await self._sleep(float(retry_after or 1))
                continue
            if code >= 500 and attempt <= MAX_RETRIES:
                await self._sleep(self._backoff * 2 ** (attempt - 1))
                continue
            if code == 400 and _is_not_modified(description):
                return None
            if code == 403:
                chat_id = params.get("chat_id")
                if self.on_bot_blocked is not None and isinstance(chat_id, int) and chat_id > 0:
                    await self.on_bot_blocked(chat_id)
                raise BotBlockedError(code, description, retry_after, method)
            raise TelegramApiError(code, description, retry_after, method)

    # --- typed helpers -------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.call("getMe")
        return result

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self.call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ALLOWED_UPDATES},
            timeout=timeout + POLL_EXTRA,
        )
        return result

    async def set_webhook(self, url: str, secret_token: str) -> Any:
        return await self.call(
            "setWebhook", {"url": url, "secret_token": secret_token, "allowed_updates": ALLOWED_UPDATES}
        )

    async def delete_webhook(self) -> Any:
        return await self.call("deleteWebhook", {"drop_pending_updates": False})

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        *,
        disable_preview: bool = True,
    ) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
                "link_preview_options": {"is_disabled": True} if disable_preview else None,
            },
        )
        return result

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> Any:
        return await self.call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict[str, Any] | None = None
    ) -> Any:
        return await self.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {"inline_keyboard": []}},
        )

    async def delete_message(self, chat_id: int, message_id: int) -> Any:
        return await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def send_photo(
        self, chat_id: int, photo: bytes, caption: str | None = None, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self.call(
            "sendPhoto",
            {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML", "reply_markup": reply_markup},
            files={"photo": ("qr.png", photo, "image/png")},
        )
        return result

    async def download_file(self, file_id: str, max_bytes: int) -> bytes | None:
        """Content of a file the user sent to the bot; None when it is larger than `max_bytes`."""
        info = await self.call("getFile", {"file_id": file_id})
        path = str((info or {}).get("file_path") or "")
        if not path or int((info or {}).get("file_size") or 0) > max_bytes:
            return None
        try:
            resp = await self._http.get(self._file_base + path)
        except httpx.HTTPError as exc:
            raise TelegramNetworkError("downloadFile", type(exc).__name__) from None
        if resp.status_code != 200:
            raise TelegramApiError(resp.status_code, "file download failed", None, "downloadFile")
        return resp.content if len(resp.content) <= max_bytes else None

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None, alert: bool = False) -> Any:
        return await self.call(
            "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text, "show_alert": alert or None}
        )

    async def set_my_commands(self, commands: Sequence[tuple[str, str]], scope: dict[str, Any] | None = None) -> Any:
        return await self.call(
            "setMyCommands",
            {"commands": [{"command": c, "description": d} for c, d in commands], "scope": scope},
        )

    async def set_chat_menu_button(self, text: str, url: str, chat_id: int | None = None) -> Any:
        return await self.call(
            "setChatMenuButton",
            {"chat_id": chat_id, "menu_button": {"type": "web_app", "text": text, "web_app": {"url": url}}},
        )


ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]
