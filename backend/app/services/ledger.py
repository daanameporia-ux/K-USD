"""The only place where balances change. Every change is an append-only ledger entry.

Callers must hold the user row lock (`users.lock`) inside the current transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import LedgerType
from app.domain.errors import ServiceError
from app.models import LedgerEntry, User

ZERO = Decimal(0)


class InsufficientFunds(ServiceError):
    def __init__(self) -> None:
        super().__init__("insufficient_funds", "Недостаточно средств на балансе.", status=409)


async def post(
    session: AsyncSession,
    user: User,
    entry_type: LedgerType,
    *,
    balance_delta: Decimal,
    hold_delta: Decimal,
    now: datetime,
    order_id: int | None = None,
    withdrawal_id: int | None = None,
    staff_id: int | None = None,
    comment: str | None = None,
) -> LedgerEntry:
    new_balance = user.balance + balance_delta
    new_hold = user.hold + hold_delta
    if new_balance < ZERO or new_hold < ZERO:
        raise InsufficientFunds()
    if entry_type == LedgerType.adjustment and not (comment and comment.strip()):
        raise ServiceError("comment_required", "Для корректировки нужен комментарий.")
    user.balance = new_balance
    user.hold = new_hold
    entry = LedgerEntry(
        user_id=user.id,
        type=entry_type,
        balance_delta=balance_delta,
        hold_delta=hold_delta,
        balance_after=new_balance,
        hold_after=new_hold,
        order_id=order_id,
        withdrawal_id=withdrawal_id,
        staff_id=staff_id,
        comment=comment,
        created_at=now,
    )
    session.add(entry)
    await session.flush()
    return entry


async def deposit(session: AsyncSession, user: User, amount: Decimal, *, order_id: int, now: datetime) -> LedgerEntry:
    if amount <= ZERO:
        raise ValueError("deposit must be positive")
    return await post(
        session, user, LedgerType.deposit, balance_delta=amount, hold_delta=ZERO, now=now, order_id=order_id
    )


async def hold(session: AsyncSession, user: User, amount: Decimal, *, withdrawal_id: int, now: datetime) -> LedgerEntry:
    if amount <= ZERO:
        raise ValueError("hold must be positive")
    return await post(
        session,
        user,
        LedgerType.withdrawal_hold,
        balance_delta=-amount,
        hold_delta=amount,
        now=now,
        withdrawal_id=withdrawal_id,
    )


async def release(
    session: AsyncSession, user: User, amount: Decimal, *, withdrawal_id: int, now: datetime
) -> LedgerEntry:
    return await post(
        session,
        user,
        LedgerType.withdrawal_release,
        balance_delta=amount,
        hold_delta=-amount,
        now=now,
        withdrawal_id=withdrawal_id,
    )


async def complete(
    session: AsyncSession, user: User, amount: Decimal, *, withdrawal_id: int, now: datetime
) -> LedgerEntry:
    return await post(
        session,
        user,
        LedgerType.withdrawal_complete,
        balance_delta=ZERO,
        hold_delta=-amount,
        now=now,
        withdrawal_id=withdrawal_id,
    )


ADJUST_COMMENT_MIN = 10


async def adjust(
    session: AsyncSession, user: User, delta: Decimal, *, staff_id: int, comment: str, now: datetime
) -> LedgerEntry:
    if delta == ZERO:
        raise ServiceError("invalid_amount", "Сумма корректировки не может быть нулевой.")
    if len((comment or "").strip()) < ADJUST_COMMENT_MIN:
        raise ServiceError("comment_required", f"Комментарий обязателен, минимум {ADJUST_COMMENT_MIN} символов.")
    if user.balance + delta < ZERO:
        raise ServiceError("negative_balance", "Итоговый баланс не может быть отрицательным.", status=409)
    return await post(
        session,
        user,
        LedgerType.adjustment,
        balance_delta=delta,
        hold_delta=ZERO,
        now=now,
        staff_id=staff_id,
        comment=comment.strip(),
    )


@dataclass(frozen=True)
class Mismatch:
    user_id: int
    balance: Decimal
    ledger_balance: Decimal
    hold: Decimal
    ledger_hold: Decimal


async def check_invariant(session: AsyncSession) -> list[Mismatch]:
    sums = (
        select(
            LedgerEntry.user_id.label("user_id"),
            func.coalesce(func.sum(LedgerEntry.balance_delta), 0).label("b"),
            func.coalesce(func.sum(LedgerEntry.hold_delta), 0).label("h"),
        )
        .group_by(LedgerEntry.user_id)
        .subquery()
    )
    b_sum = func.coalesce(sums.c.b, 0)
    h_sum = func.coalesce(sums.c.h, 0)
    stmt = (
        select(User.id, User.balance, b_sum, User.hold, h_sum)
        .outerjoin(sums, sums.c.user_id == User.id)
        .where((User.balance != b_sum) | (User.hold != h_sum))
    )
    rows = (await session.execute(stmt)).all()
    return [Mismatch(int(r[0]), Decimal(r[1]), Decimal(r[2]), Decimal(r[3]), Decimal(r[4])) for r in rows]


@dataclass(frozen=True)
class BalanceInfo:
    available: Decimal
    hold: Decimal
    total_bought: Decimal
    total_withdrawn: Decimal


async def summary(session: AsyncSession, user: User) -> BalanceInfo:
    deposits = func.coalesce(func.sum(LedgerEntry.balance_delta).filter(LedgerEntry.type == LedgerType.deposit), 0)
    withdrawn = func.coalesce(
        func.sum(-LedgerEntry.hold_delta).filter(LedgerEntry.type == LedgerType.withdrawal_complete), 0
    )
    row = (await session.execute(select(deposits, withdrawn).where(LedgerEntry.user_id == user.id))).one()
    return BalanceInfo(user.balance, user.hold, Decimal(row[0]), Decimal(row[1]))
