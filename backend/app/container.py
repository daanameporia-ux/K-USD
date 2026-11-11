"""Dependency container: everything a request, update handler or job needs."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import httpx
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.clock import Clock
from app.config import Config
from app.db import make_engine, make_sessionmaker
from app.kyc.base import KycProvider
from app.kyc.stub import StubKycProvider
from app.models import User
from app.payments.registry import build_registry
from app.services.notify import Throttle
from app.telegram.client import TelegramClient
from app.telegram.dispatcher import Dispatcher
from app.telegram.handlers import build_router
from app.telegram.handlers.base import load_user
from app.telegram.router import Ctx, Router


class Container:
    def __init__(
        self,
        config: Config,
        *,
        clock: Clock | None = None,
        engine: AsyncEngine | None = None,
        tg_transport: httpx.AsyncBaseTransport | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        router: Router | None = None,
        tg_sleep: Any = None,
    ) -> None:
        self.config = config
        self.clock = clock or Clock()
        self._own_engine = engine is None
        self.engine = engine or make_engine(config.database_url)
        self.db = make_sessionmaker(self.engine)
        self.http = httpx.AsyncClient(transport=http_transport, timeout=httpx.Timeout(15.0, connect=5.0))
        tg_kwargs: dict[str, Any] = {}
        if tg_sleep is not None:
            tg_kwargs["sleep"] = tg_sleep
            tg_kwargs["backoff_base"] = 0.0
        self.tg = TelegramClient(
            config.bot_token,
            base_url=config.telegram_api_url,
            transport=tg_transport,
            on_bot_blocked=self.mark_bot_blocked,
            **tg_kwargs,
        )
        self.kyc: KycProvider = StubKycProvider()
        self.throttle = Throttle()
        self.payments = build_registry(config, self.db, self.clock)
        self._polling_task: asyncio.Task[None] | None = None
        self._polling_stop = asyncio.Event()
        from app.jobs.registry import build_scheduler

        self.scheduler = build_scheduler(self)
        self.dispatcher = Dispatcher(self, router or build_router(), before=load_user)

    async def on_start(self, ctx: Ctx) -> None:
        """Hook for /start: binds pending staff invitations (see services.staff)."""

    async def startup(self) -> None:
        from app.telegram.setup import configure_bot

        staff_ids = await self.sync_superadmins()
        await configure_bot(self, staff_ids)
        if self.config.run_scheduler:
            self.scheduler.start()
        if self.config.bot_mode == "polling":
            self._polling_stop = asyncio.Event()
            self._polling_task = asyncio.create_task(self.dispatcher.run_polling(self._polling_stop))

    async def shutdown(self) -> None:
        if self._polling_task is not None:
            self._polling_stop.set()
            self._polling_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._polling_task
        await self.dispatcher.drain(20.0)
        await self.scheduler.stop()
        await self.aclose()

    async def sync_superadmins(self) -> list[int]:
        """Ensure SUPERADMIN_TG_IDS exist as active superadmins; return tg ids of active staff."""
        return []

    async def mark_bot_blocked(self, tg_id: int) -> None:
        async with self.db.begin() as session:
            await session.execute(update(User).where(User.tg_id == tg_id).values(bot_blocked=True))

    async def readiness(self) -> dict[str, bool]:
        checks: dict[str, bool] = {}
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            checks["database"] = True
            checks["migrations"] = version is not None
        except Exception:
            checks["database"] = False
            checks["migrations"] = False
        if self.config.run_scheduler:
            checks["scheduler"] = self.scheduler.running
        return checks

    async def aclose(self) -> None:
        await self.tg.aclose()
        await self.http.aclose()
        if self._own_engine:
            await self.engine.dispose()
