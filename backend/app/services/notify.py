"""Outbox of notifications: written in the same transaction as the business change, sent by a job."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationOutbox

STAFF_CHAT = 0  # chat_id placeholder: resolved at send time to STAFF_CHAT_ID or to all active admins


async def enqueue(
    session: AsyncSession, chat_id: int, method: str, params: dict[str, Any], *, now: datetime
) -> NotificationOutbox:
    row = NotificationOutbox(
        chat_id=chat_id,
        payload={"method": method, "params": params},
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def message(
    session: AsyncSession,
    chat_id: int,
    text: str,
    *,
    now: datetime,
    reply_markup: dict[str, Any] | None = None,
) -> NotificationOutbox:
    params: dict[str, Any] = {"text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return await enqueue(session, chat_id, "sendMessage", params, now=now)


async def remove_buttons(session: AsyncSession, chat_id: int, message_id: int, *, now: datetime) -> None:
    await enqueue(
        session,
        chat_id,
        "editMessageReplyMarkup",
        {"message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        now=now,
    )


async def alert(session: AsyncSession, text: str, *, now: datetime) -> NotificationOutbox:
    """Alert for staff (служебный чат)."""
    return await message(session, STAFF_CHAT, text, now=now)


class Throttle:
    """In-process throttle for repeating alerts (single-instance service)."""

    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}

    def allow(self, key: str, now: datetime, every: timedelta = timedelta(minutes=10)) -> bool:
        last = self._last.get(key)
        if last is not None and now - last < every:
            return False
        self._last[key] = now
        return True

    def reset(self, key: str) -> None:
        self._last.pop(key, None)
