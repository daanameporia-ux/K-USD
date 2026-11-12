"""Merged feed of a client's purchases and withdrawals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import literal, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Network, WithdrawalStatus
from app.domain.errors import ServiceError
from app.models import Order, Withdrawal
from app.services.labels import ORDER_STATUS, WITHDRAWAL_STATUS


def explorer_url(network: Network, tx_hash: str) -> str:
    if network == Network.TRC20:
        return f"https://tronscan.org/#/transaction/{tx_hash}"
    return f"https://bscscan.com/tx/{tx_hash}"


@dataclass(frozen=True)
class HistoryItem:
    kind: Literal["order", "withdrawal"]
    id: int
    public_id: str
    created_at: datetime
    status: str
    status_label: str
    usdt: Decimal
    amount_rub: int | None = None
    rate: Decimal | None = None
    network: Network | None = None
    address: str | None = None
    fee: Decimal | None = None
    amount_to_send: Decimal | None = None
    tx_hash: str | None = None
    explorer_url: str | None = None
    reject_reason: str | None = None


def _encode(created_at: datetime, kind: str, id_: int) -> str:
    return f"{created_at.isoformat()}~{kind}~{id_}"


def _decode(cursor: str) -> tuple[datetime, str, int]:
    try:
        ts, kind, id_ = cursor.split("~")
        return datetime.fromisoformat(ts), kind, int(id_)
    except ValueError as exc:
        raise ServiceError("bad_cursor", "Некорректный курсор") from exc


async def feed(
    session: AsyncSession, user_id: int, *, limit: int = 20, cursor: str | None = None, offset: int = 0
) -> tuple[list[HistoryItem], str | None]:
    orders_q = select(literal("order").label("kind"), Order.id.label("id"), Order.created_at.label("created_at")).where(
        Order.user_id == user_id
    )
    wd_q = select(
        literal("withdrawal").label("kind"), Withdrawal.id.label("id"), Withdrawal.created_at.label("created_at")
    ).where(Withdrawal.user_id == user_id)
    u = union_all(orders_q, wd_q).subquery()
    stmt = select(u.c.kind, u.c.id, u.c.created_at)
    if cursor:
        ts, kind, id_ = _decode(cursor)
        stmt = stmt.where(tuple_(u.c.created_at, u.c.kind, u.c.id) < tuple_(literal(ts), literal(kind), literal(id_)))
    stmt = stmt.order_by(u.c.created_at.desc(), u.c.kind.desc(), u.c.id.desc()).offset(offset).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    order_ids = [r.id for r in rows if r.kind == "order"]
    wd_ids = [r.id for r in rows if r.kind == "withdrawal"]
    orders = {o.id: o for o in (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars()}
    wds = {w.id: w for w in (await session.execute(select(Withdrawal).where(Withdrawal.id.in_(wd_ids)))).scalars()}
    items: list[HistoryItem] = []
    for r in rows:
        if r.kind == "order":
            o = orders[r.id]
            items.append(
                HistoryItem(
                    kind="order",
                    id=o.id,
                    public_id=o.public_id,
                    created_at=o.created_at,
                    status=o.status.value,
                    status_label=ORDER_STATUS[o.status],
                    usdt=o.credited_usdt if o.credited_usdt is not None else o.usdt_amount,
                    amount_rub=o.amount_rub,
                    rate=o.rate,
                )
            )
        else:
            w = wds[r.id]
            done = w.status == WithdrawalStatus.completed
            items.append(
                HistoryItem(
                    kind="withdrawal",
                    id=w.id,
                    public_id=w.public_id,
                    created_at=w.created_at,
                    status=w.status.value,
                    status_label=WITHDRAWAL_STATUS[w.status],
                    usdt=w.amount,
                    network=w.network,
                    address=w.address,
                    fee=w.fee,
                    amount_to_send=w.amount_to_send,
                    tx_hash=w.tx_hash if done else None,
                    explorer_url=explorer_url(w.network, w.tx_hash) if (w.tx_hash and done) else None,
                    reject_reason=w.reject_reason,
                )
            )
    next_cursor = _encode(rows[-1].created_at, rows[-1].kind, rows[-1].id) if has_more and rows else None
    return items, next_cursor
