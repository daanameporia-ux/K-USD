"""Payment screenshots: what the client still owes and what staff must review before a withdrawal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import OrderStatus
from app.domain.errors import Conflict, NotFound, ServiceError
from app.models import Order, OrderProof, User
from app.services import audit, messages, notify

if TYPE_CHECKING:
    from app.container import Container

REVIEW_LIST_LIMIT = 200


def _awaiting(user: User) -> list[ColumnElement[bool]]:
    """Paid orders of the client whose screenshot is not reviewed yet.

    A missing screenshot counts only while the requirement is on for the client: switching it off in the
    admin panel releases the orders that were waiting for a file.
    """
    conditions: list[ColumnElement[bool]] = [
        Order.user_id == user.id,
        Order.status == OrderStatus.paid,
        Order.proof_required.is_(True),
        Order.proof_reviewed_at.is_(None),
    ]
    if not user.require_payment_proof:
        conditions.append(Order.proof_uploaded_at.is_not(None))
    return conditions


async def pending_proof(session: AsyncSession, user: User) -> Order | None:
    """The oldest paid order of the client that still waits for the payment screenshot."""
    if not user.require_payment_proof:
        return None
    stmt = (
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.status == OrderStatus.paid,
            Order.proof_required.is_(True),
            Order.proof_uploaded_at.is_(None),
        )
        .order_by(Order.id)
        .limit(1)
    )
    result: Order | None = (await session.execute(stmt)).scalar_one_or_none()
    return result


@dataclass(frozen=True)
class ReviewState:
    to_review: int  # uploaded, waits for a staff member
    missing: int  # the client has not uploaded the file yet
    reviewed: int

    @property
    def blocking(self) -> int:
        return self.to_review + self.missing


async def review_state(session: AsyncSession, user: User) -> ReviewState:
    uploaded = Order.proof_uploaded_at.is_not(None)
    row = (
        await session.execute(
            select(
                func.count().filter(uploaded),
                func.count().filter(~uploaded),
            ).where(*_awaiting(user))
        )
    ).one()
    reviewed = (
        await session.execute(
            select(func.count())
            .select_from(Order)
            .where(Order.user_id == user.id, Order.status == OrderStatus.paid, Order.proof_reviewed_at.is_not(None))
        )
    ).scalar_one()
    return ReviewState(to_review=int(row[0]), missing=int(row[1]), reviewed=int(reviewed))


async def awaiting_review(session: AsyncSession, user: User) -> list[Order]:
    stmt = select(Order).where(*_awaiting(user)).order_by(Order.id.desc()).limit(REVIEW_LIST_LIMIT)
    return list((await session.execute(stmt)).scalars())


def blocked_message(state: ReviewState) -> str:
    parts = []
    if state.to_review:
        parts.append(f"не проверено скриншотов: {state.to_review}")
    if state.missing:
        parts.append(f"клиент не загрузил скриншотов: {state.missing}")
    return "Сначала проверьте скриншоты оплат клиента (" + ", ".join(parts) + ")."


async def ensure_reviewed(session: AsyncSession, user_id: int) -> None:
    """A withdrawal moves on only after staff reviewed the screenshots of all paid orders of the client."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("Клиент не найден")
    state = await review_state(session, user)
    if state.blocking:
        raise Conflict(
            "proofs_not_reviewed",
            blocked_message(state),
            details={"to_review": state.to_review, "missing": state.missing},
        )


async def _lock(session: AsyncSession, order_id: int) -> Order:
    stmt = select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
    order: Order | None = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise NotFound("Заявка не найдена")
    return order


async def review(c: Container, order_id: int, staff_id: int, ip: str | None = None) -> Order:
    """A staff member looked at the screenshot and accepts it."""
    now = c.clock.now()
    async with c.db.begin() as session:
        order = await _lock(session, order_id)
        if order.proof_uploaded_at is None:
            raise Conflict("proof_missing", "Клиент ещё не загрузил скриншот по этой заявке.")
        if order.proof_reviewed_at is None:
            order.proof_reviewed_at = now
            order.proof_reviewed_by = staff_id
            await audit.write(
                session, action="order.proof_review", staff_id=staff_id, entity_type="order", entity_id=order.id, ip=ip
            )
        return order


async def reject(c: Container, order_id: int, staff_id: int, comment: str, ip: str | None = None) -> Order:
    """The screenshot is not acceptable: it is removed and the client is asked for a new one."""
    comment = (comment or "").strip()
    if len(comment) < 3:
        raise ServiceError("comment_required", "Укажите, что не так со скриншотом — клиент увидит этот текст.")
    now = c.clock.now()
    async with c.db.begin() as session:
        order = await _lock(session, order_id)
        if order.proof_uploaded_at is None:
            raise Conflict("proof_missing", "Клиент ещё не загрузил скриншот по этой заявке.")
        user = await session.get(User, order.user_id)
        await session.execute(delete(OrderProof).where(OrderProof.order_id == order.id))
        order.proof_uploaded_at = None
        order.proof_reviewed_at = None
        order.proof_reviewed_by = None
        await audit.write(
            session,
            action="order.proof_reject",
            staff_id=staff_id,
            entity_type="order",
            entity_id=order.id,
            comment=comment,
            ip=ip,
        )
        if user is not None:
            await notify.message(session, user.tg_id, messages.proof_rejected(order.public_id, comment), now=now)
        return order
