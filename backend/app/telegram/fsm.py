"""Dialog state persisted in the database so it survives restarts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import FsmState


class FSM:
    def __init__(
        self, db: async_sessionmaker[AsyncSession], tg_id: int, state: str | None, data: dict[str, Any]
    ) -> None:
        self._db = db
        self.tg_id = tg_id
        self.state = state
        self.data = data

    @classmethod
    async def load(cls, db: async_sessionmaker[AsyncSession], tg_id: int) -> FSM:
        async with db() as session:
            row = (await session.execute(select(FsmState).where(FsmState.tg_id == tg_id))).scalar_one_or_none()
        if row is None:
            return cls(db, tg_id, None, {})
        return cls(db, tg_id, row.state, dict(row.data or {}))

    async def _save(self) -> None:
        now = datetime.now(UTC)
        stmt = insert(FsmState).values(tg_id=self.tg_id, state=self.state, data=self.data, updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[FsmState.tg_id], set_={"state": self.state, "data": self.data, "updated_at": now}
        )
        async with self._db.begin() as session:
            await session.execute(stmt)

    async def set(self, state: str | None, data: dict[str, Any] | None = None) -> None:
        self.state = state
        if data is not None:
            self.data = dict(data)
        await self._save()

    async def update(self, **values: Any) -> None:
        self.data.update(values)
        await self._save()

    async def clear(self) -> None:
        self.state = None
        self.data = {}
        async with self._db.begin() as session:
            await session.execute(delete(FsmState).where(FsmState.tg_id == self.tg_id))
