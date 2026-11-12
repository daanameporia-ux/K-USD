"""Update intake: deduplication, per-user ordering, error isolation, webhook and polling."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from app.logging import get_logger
from app.models import TgUpdate
from app.telegram.client import BotBlockedError, TelegramApiError
from app.telegram.fsm import FSM
from app.telegram.router import Ctx, Router
from app.telegram.types import Update

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)

ERROR_TEXT = "Что-то пошло не так, попробуйте ещё раз."

BeforeHook = Callable[[Ctx], Awaitable[bool]]


class KeyedLock:
    """asyncio locks keyed by an id; entries are removed when no longer used."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._users: dict[int, int] = {}

    @asynccontextmanager
    async def __call__(self, key: int) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._users[key] -= 1
            if self._users[key] == 0:
                del self._users[key]
                del self._locks[key]

    def __len__(self) -> int:
        return len(self._locks)


class Dispatcher:
    def __init__(self, container: Container, router: Router, before: BeforeHook | None = None) -> None:
        self.c = container
        self.router = router
        self.before = before
        self.locks = KeyedLock()
        self._tasks: set[asyncio.Task[None]] = set()
        self.accepting = True

    async def _mark_seen(self, update_id: int) -> bool:
        stmt = (
            insert(TgUpdate)
            .values(update_id=update_id, received_at=self.c.clock.now())
            .on_conflict_do_nothing(index_elements=[TgUpdate.update_id])
            .returning(TgUpdate.update_id)
        )
        async with self.c.db.begin() as session:
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    def spawn(self, raw: dict[str, Any]) -> asyncio.Task[None]:
        task = asyncio.create_task(self.process(raw))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float = 20.0) -> None:
        self.accepting = False
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    async def process(self, raw: dict[str, Any]) -> None:
        """Process one raw update. Never raises."""
        update_id = raw.get("update_id")
        with structlog.contextvars.bound_contextvars(update_id=update_id):
            try:
                update = Update.model_validate(raw)
            except ValidationError:
                log.warning("tg_update_invalid")
                return
            user = update.from_user
            chat = update.chat
            if user is None or chat is None or user.is_bot or chat.type != "private":
                return
            # The lock is taken before any await so updates of one user keep their arrival order.
            async with self.locks(user.id):
                try:
                    if not await self._mark_seen(update.update_id):
                        log.info("tg_update_duplicate")
                        return
                except Exception:
                    log.exception("tg_update_dedup_failed")
                    return
                await self._handle(update, user.id, chat.id)

    async def _handle(self, update: Update, tg_id: int, chat_id: int) -> None:
        ctx: Ctx | None = None
        try:
            fsm = await FSM.load(self.c.db, tg_id)
            ctx = Ctx(update=update, container=self.c, tg=self.c.tg, tg_id=tg_id, chat_id=chat_id, fsm=fsm)
            if self.before is not None and not await self.before(ctx):
                await ctx.answer()
                return
            handler, resets = self.router.resolve(ctx)
            if handler is None:
                return
            if resets and ctx.fsm.state is not None:
                await ctx.fsm.clear()
            await handler(ctx)
            await ctx.answer()
        except BotBlockedError:
            log.info("tg_bot_blocked_by_user", tg_id=tg_id)
        except Exception:
            log.exception("tg_handler_failed", tg_id=tg_id)
            with suppress(Exception):
                if ctx is not None:
                    await ctx.answer()
                await self.c.tg.send_message(chat_id, ERROR_TEXT)

    async def run_polling(self, stop: asyncio.Event) -> None:
        with suppress(TelegramApiError):
            await self.c.tg.delete_webhook()
        offset: int | None = None
        while not stop.is_set():
            try:
                updates = await self.c.tg.get_updates(offset, timeout=30)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("tg_polling_error", exc_info=True)
                await asyncio.sleep(3)
                continue
            if not updates:
                # long polling returns an empty list only after its timeout; never spin
                await asyncio.sleep(0.1)
            for raw in updates:
                offset = int(raw["update_id"]) + 1
                self.spawn(raw)

    async def cleanup_seen(self, older_than: timedelta = timedelta(days=7)) -> int:
        cutoff = self.c.clock.now() - older_than
        async with self.c.db.begin() as session:
            result = await session.execute(delete(TgUpdate).where(TgUpdate.received_at < cutoff))
        return int(getattr(result, "rowcount", 0) or 0)
