"""Antifraud orchestration (rules themselves are pure functions in domain.antifraud)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Withdrawal
from app.services.settings import BizSettings


async def check_order_creation(
    session: AsyncSession, user: User, biz: BizSettings, amount_rub: int | None, now: datetime
) -> None:
    """Raise ServiceError if a new order is not allowed by B2/B3/B9/B10."""


async def on_order_finished(session: AsyncSession, user: User, biz: BizSettings, now: datetime) -> None:
    """Recalculate A1–A3 after a final order transition."""


async def check_withdrawal(session: AsyncSession, user: User, biz: BizSettings, amount: Decimal, now: datetime) -> None:
    """Raise ServiceError if the withdrawal is not allowed by B14."""


async def on_withdrawal_created(
    session: AsyncSession, user: User, withdrawal: Withdrawal, biz: BizSettings, now: datetime
) -> None:
    """B4 (shared address) and B12 (fast transit)."""
