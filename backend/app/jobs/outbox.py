from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.domain.enums import StaffRole
from app.logging import get_logger
from app.models import NotificationOutbox, Staff
from app.services.notify import STAFF_CHAT
from app.telegram.client import BotBlockedError, TelegramApiError

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)

MAX_ATTEMPTS = 10
LEASE = timedelta(minutes=2)


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * 2**attempts, 900))


async def _staff_recipients(c: Container) -> list[int]:
    if c.config.staff_chat_id is not None:
        return [c.config.staff_chat_id]
    async with c.db() as session:
        rows = await session.execute(
            select(Staff.tg_id).where(
                Staff.is_active.is_(True), Staff.role == StaffRole.admin, Staff.tg_id.is_not(None)
            )
        )
        return [int(r[0]) for r in rows if r[0] is not None]


async def _send(c: Container, method: str, params: dict[str, Any], chat_id: int) -> None:
    try:
        await c.tg.call(method, {**params, "chat_id": chat_id})
    except TelegramApiError as exc:
        # A text that Telegram cannot parse as HTML must still reach the reader: resend it as plain text.
        if exc.code == 400 and "parse entities" in exc.description.lower() and params.get("parse_mode"):
            plain = {k: v for k, v in params.items() if k != "parse_mode"}
            await c.tg.call(method, {**plain, "chat_id": chat_id})
            return
        raise


async def send_notifications(c: Container, batch: int = 50) -> int:
    now = c.clock.now()
    async with c.db.begin() as session:
        stmt = (
            select(NotificationOutbox)
            .where(
                NotificationOutbox.sent_at.is_(None),
                NotificationOutbox.failed.is_(False),
                NotificationOutbox.next_attempt_at <= now,
            )
            .order_by(NotificationOutbox.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars())
        for row in rows:
            row.next_attempt_at = now + LEASE
    sent = 0
    for row in rows:
        method = str(row.payload["method"])
        params = dict(row.payload.get("params") or {})
        error: str | None = None
        permanent = False
        delivered: list[int] | None = None
        if row.chat_id == STAFF_CHAT:
            delivered = [int(x) for x in row.payload.get("delivered") or []]
            recipients = [r for r in await _staff_recipients(c) if r not in delivered]
            if not recipients and not delivered:
                error = "no staff recipients (STAFF_CHAT_ID is empty and no active admin has a Telegram ID)"
            for chat_id in recipients:
                try:
                    await _send(c, method, params, chat_id)
                    delivered.append(chat_id)
                except TelegramApiError as exc:
                    log.warning("staff_alert_failed", chat_id=chat_id, code=exc.code)
                    if isinstance(exc, BotBlockedError) or (400 <= exc.code < 500 and exc.code != 429):
                        delivered.append(chat_id)  # this recipient can never get it; do not retry for them
                    else:
                        error = f"{exc.code}: {exc.description}"  # retry the remaining recipients later
        else:
            try:
                await _send(c, method, params, row.chat_id)
            except BotBlockedError as exc:
                error, permanent = exc.description, True
            except TelegramApiError as exc:
                error = f"{exc.code}: {exc.description}"
                permanent = 400 <= exc.code < 500 and exc.code != 429
        async with c.db.begin() as session:
            fresh = await session.get(NotificationOutbox, row.id, with_for_update=True)
            if fresh is None:
                continue
            fresh.attempts += 1
            if delivered is not None:
                fresh.payload = {**fresh.payload, "delivered": delivered}
            if error is None:
                fresh.sent_at = c.clock.now()
                sent += 1
            else:
                fresh.last_error = error[:500]
                if permanent or fresh.attempts >= MAX_ATTEMPTS:
                    fresh.failed = True
                else:
                    fresh.next_attempt_at = c.clock.now() + backoff(fresh.attempts)
    return sent
