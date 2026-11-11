"""Antifraud orchestration (rules themselves are pure functions in domain.antifraud)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.settings import BizSettings


async def check_order_creation(
    session: AsyncSession, user: User, biz: BizSettings, amount_rub: int | None, now: datetime
) -> None:
    """Raise ServiceError if a new order is not allowed by B2/B3/B9/B10."""


async def on_order_finished(session: AsyncSession, user: User, biz: BizSettings, now: datetime) -> None:
    """Recalculate A1–A3 after a final order transition."""
