"""Dependency container: everything a request, update handler or job needs."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.clock import Clock
from app.config import Config
from app.db import make_engine, make_sessionmaker
from app.models import User
from app.telegram.client import TelegramClient
from app.telegram.dispatcher import Dispatcher
from app.telegram.router import Router


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
        self.dispatcher = Dispatcher(self, router or Router())

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
        return checks

    async def aclose(self) -> None:
        await self.tg.aclose()
        await self.http.aclose()
        if self._own_engine:
            await self.engine.dispose()
