"""Statistics computed on demand with SQL (day boundaries in Europe/Moscow)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import antifraud as rules
from app.domain.enums import OrderStatus, WithdrawalStatus
from app.domain.timefmt import MSK, msk_date
from app.models import Order, User, Withdrawal
from app.services import antifraud
from app.services import rates as rates_svc
from app.services.settings import BizSettings

S = OrderStatus
ZERO = Decimal(0)


def _msk_day(column: Any) -> Any:
    return cast(func.timezone("Europe/Moscow", column), Date)


def msk_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=MSK)


@dataclass(frozen=True)
class Counts:
    created: int = 0
    paid: int = 0
    expired: int = 0
    cancelled: int = 0
    rejected: int = 0
    review: int = 0
    failed: int = 0
    pending: int = 0

    @property
    def finished(self) -> int:
        return self.paid + self.expired + self.cancelled + self.rejected

    @property
    def conversion_pct(self) -> Decimal | None:
        return rules.conversion_pct(self.paid, self.finished)


def _count_columns() -> list[Any]:
    return [
        func.count(Order.id),
        func.count(Order.id).filter(Order.status == S.paid),
        func.count(Order.id).filter(Order.status == S.expired),
        func.count(Order.id).filter(Order.status == S.cancelled),
        func.count(Order.id).filter(Order.status == S.rejected),
        func.count(Order.id).filter(Order.status == S.review),
        func.count(Order.id).filter(Order.status == S.failed),
        func.count(Order.id).filter(Order.status.in_((S.pending, S.creating))),
    ]


def _paid_rub() -> Any:
    return func.coalesce(Order.paid_amount_rub, Order.amount_rub)


def _money_columns() -> list[Any]:
    paid = Order.status == S.paid
    return [
        func.coalesce(func.sum(_paid_rub()).filter(paid), 0),
        func.coalesce(func.sum(Order.credited_usdt).filter(paid), 0),
        func.coalesce(func.sum(Order.margin_rub).filter(paid), 0),
    ]


def _counts(row: Any, offset: int = 0) -> Counts:
    return Counts(*(int(row[offset + i]) for i in range(8)))


@dataclass(frozen=True)
class ClientStats:
    counts: Counts
    conversion_pct: Decimal | None
    window_size: int
    window_finished: int
    window_conversion_pct: Decimal | None
    unpaid_streak: int
    turnover_rub: int
    usdt_received: Decimal
    margin_rub: Decimal
    avg_check_rub: Decimal | None
    median_pay_seconds: int | None
    withdrawals_count: int
    withdrawals_sum: Decimal
    withdrawals_fees: Decimal
    first_deal_at: datetime | None
    last_deal_at: datetime | None


async def client_stats(session: AsyncSession, user: User, biz: BizSettings) -> ClientStats:
    row = (
        await session.execute(
            select(
                *_count_columns(),
                *_money_columns(),
                func.percentile_cont(0.5)
                .within_group(func.extract("epoch", Order.paid_at - Order.created_at))
                .filter(Order.status == S.paid),
                func.min(Order.paid_at).filter(Order.status == S.paid),
                func.max(Order.paid_at).filter(Order.status == S.paid),
            ).where(Order.user_id == user.id)
        )
    ).one()
    counts = _counts(row)
    turnover = int(row[8])
    wd = (
        await session.execute(
            select(
                func.count(Withdrawal.id),
                func.coalesce(func.sum(Withdrawal.amount), 0),
                func.coalesce(func.sum(Withdrawal.fee), 0),
            ).where(Withdrawal.user_id == user.id, Withdrawal.status == WithdrawalStatus.completed)
        )
    ).one()
    finished = await antifraud.finished_orders(session, user, max(biz.af_window_orders, 1000))
    window = rules.only_finished(finished)[: biz.af_window_orders]
    window_paid = sum(1 for o in window if o.status == S.paid)
    return ClientStats(
        counts=counts,
        conversion_pct=counts.conversion_pct,
        window_size=biz.af_window_orders,
        window_finished=len(window),
        window_conversion_pct=rules.conversion_pct(window_paid, len(window)),
        unpaid_streak=rules.unpaid_streak(rules.only_finished(finished)),
        turnover_rub=turnover,
        usdt_received=Decimal(row[9]),
        margin_rub=Decimal(row[10]),
        avg_check_rub=(Decimal(turnover) / counts.paid).quantize(Decimal("0.01")) if counts.paid else None,
        median_pay_seconds=int(row[11]) if row[11] is not None else None,
        withdrawals_count=int(wd[0]),
        withdrawals_sum=Decimal(wd[1]),
        withdrawals_fees=Decimal(wd[2]),
        first_deal_at=row[12],
        last_deal_at=row[13],
    )


@dataclass(frozen=True)
class DayStats:
    day: date
    counts: Counts
    turnover_rub: int
    usdt: Decimal
    margin_rub: Decimal

    @property
    def conversion_pct(self) -> Decimal | None:
        return self.counts.conversion_pct


async def client_daily(session: AsyncSession, user_id: int) -> list[DayStats]:
    """Per MSK day of order creation (newest first)."""
    day = _msk_day(Order.created_at).label("day")
    rows = (
        await session.execute(
            select(day, *_count_columns(), *_money_columns())
            .where(Order.user_id == user_id)
            .group_by(day)
            .order_by(day.desc())
        )
    ).all()
    return [DayStats(r[0], _counts(r, 1), int(r[9]), Decimal(r[10]), Decimal(r[11])) for r in rows]


# --- dashboard ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodStats:
    start: datetime
    end: datetime
    orders: Counts
    turnover_rub: int
    usdt_issued: Decimal
    margin_rub: Decimal
    new_clients: int
    active_clients: int
    withdrawals_completed: int
    withdrawals_completed_sum: Decimal
    fees_collected: Decimal

    @property
    def conversion_pct(self) -> Decimal | None:
        return self.orders.conversion_pct


async def period_stats(session: AsyncSession, start: datetime, end: datetime) -> PeriodStats:
    """Orders created in [start, end): counts; money by paid_at in the period."""
    counts_row = (
        await session.execute(select(*_count_columns()).where(Order.created_at >= start, Order.created_at < end))
    ).one()
    money = (await session.execute(select(*_money_columns()).where(Order.paid_at >= start, Order.paid_at < end))).one()
    new_clients = (
        await session.execute(select(func.count(User.id)).where(User.registered_at >= start, User.registered_at < end))
    ).scalar_one()
    active = (
        await session.execute(
            select(func.count(func.distinct(Order.user_id))).where(Order.created_at >= start, Order.created_at < end)
        )
    ).scalar_one()
    wd = (
        await session.execute(
            select(
                func.count(Withdrawal.id),
                func.coalesce(func.sum(Withdrawal.amount_to_send), 0),
                func.coalesce(func.sum(Withdrawal.fee), 0),
            ).where(
                Withdrawal.status == WithdrawalStatus.completed,
                Withdrawal.finished_at >= start,
                Withdrawal.finished_at < end,
            )
        )
    ).one()
    return PeriodStats(
        start=start,
        end=end,
        orders=_counts(counts_row),
        turnover_rub=int(money[0]),
        usdt_issued=Decimal(money[1]),
        margin_rub=Decimal(money[2]),
        new_clients=int(new_clients),
        active_clients=int(active),
        withdrawals_completed=int(wd[0]),
        withdrawals_completed_sum=Decimal(wd[1]),
        fees_collected=Decimal(wd[2]),
    )


@dataclass(frozen=True)
class Dashboard:
    periods: dict[str, PeriodStats]
    queue_count: int
    queue_sum: Decimal
    liabilities_balance: Decimal
    liabilities_hold: Decimal
    rate: rates_svc.CurrentRate | None
    last_rate_fetched_at: datetime | None
    af_blocked: int
    suspicious: int
    extra: dict[str, Any] = field(default_factory=dict)


async def dashboard(session: AsyncSession, biz: BizSettings, now: datetime) -> Dashboard:
    today = msk_date(now)
    t0 = msk_midnight(today)
    periods = {
        "today": await period_stats(session, t0, now + timedelta(seconds=1)),
        "yesterday": await period_stats(session, msk_midnight(today - timedelta(days=1)), t0),
        "7d": await period_stats(session, msk_midnight(today - timedelta(days=6)), now + timedelta(seconds=1)),
        "30d": await period_stats(session, msk_midnight(today - timedelta(days=29)), now + timedelta(seconds=1)),
    }
    queue = (
        await session.execute(
            select(func.count(Withdrawal.id), func.coalesce(func.sum(Withdrawal.amount_to_send), 0)).where(
                Withdrawal.status.in_(
                    (WithdrawalStatus.pending, WithdrawalStatus.in_progress, WithdrawalStatus.verifying)
                )
            )
        )
    ).one()
    liab = (
        await session.execute(select(func.coalesce(func.sum(User.balance), 0), func.coalesce(func.sum(User.hold), 0)))
    ).one()
    flags = (
        await session.execute(
            select(
                # blocked by antifraud rules (manual blocks by an admin are prefixed "Вручную")
                func.count(User.id).filter(
                    User.can_create_orders.is_(False),
                    func.coalesce(User.orders_block_reason, "").not_like("Вручную%"),
                ),
                func.count(User.id).filter(User.is_suspicious.is_(True)),
            )
        )
    ).one()
    latest = await rates_svc.latest(session)
    return Dashboard(
        periods=periods,
        queue_count=int(queue[0]),
        queue_sum=Decimal(queue[1]),
        liabilities_balance=Decimal(liab[0]),
        liabilities_hold=Decimal(liab[1]),
        rate=await rates_svc.current(session, biz, now),
        last_rate_fetched_at=latest.fetched_at if latest else None,
        af_blocked=int(flags[0]),
        suspicious=int(flags[1]),
    )


async def dashboard_daily(session: AsyncSession, now: datetime, days: int = 30) -> list[DayStats]:
    since = msk_midnight(msk_date(now) - timedelta(days=days - 1))
    day = _msk_day(Order.created_at).label("day")
    rows = (
        await session.execute(
            select(day, *_count_columns(), *_money_columns())
            .where(Order.created_at >= since)
            .group_by(day)
            .order_by(day.desc())
        )
    ).all()
    return [DayStats(r[0], _counts(r, 1), int(r[9]), Decimal(r[10]), Decimal(r[11])) for r in rows]
