"""Purchase orders: preview, creation, provider callbacks, expiry, review decisions."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.amounts import rub_range_message
from app.domain.enums import ACTIVE_ORDER_STATUSES, OrderSource, OrderStatus
from app.domain.errors import Forbidden, NotFound, ServiceError
from app.domain.pricing import Quote, quote
from app.domain.statuses import order_transition
from app.logging import get_logger
from app.models import Order, PaymentCallback, User
from app.payments.base import CallbackEvent, PaymentProvider
from app.services import antifraud, audit, ledger, messages, notify, rates
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.services.settings import BizSettings

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)

PROVIDER_TIMEOUT = 20.0
STUCK_CREATING_AFTER = timedelta(minutes=2)
RECONCILE_GRACE = timedelta(minutes=10)

MSG_PURCHASES_OFF = "Покупка временно приостановлена. Попробуйте позже."
MSG_ORDERS_BLOCKED = "Создание заявок приостановлено. Обратитесь в поддержку."
MSG_PROVIDER_FAILED = "Не удалось создать платёж, попробуйте ещё раз."


class ActiveOrderExists(ServiceError):
    def __init__(self, order: Order) -> None:
        super().__init__(
            "active_order_exists",
            f"У вас уже есть активная заявка {order.public_id}. Оплатите или отмените её.",
            status=409,
            details={"order_id": order.id, "public_id": order.public_id},
        )
        self.order = order


class PriceChanged(ServiceError):
    def __init__(self, new_quote: Quote) -> None:
        super().__init__(
            "price_changed",
            "Курс изменился. Проверьте новую сумму к зачислению и подтвердите ещё раз.",
            status=409,
            details={"usdt_amount": str(new_quote.usdt_amount), "rate": str(new_quote.rate)},
        )
        self.quote = new_quote


@dataclass(frozen=True)
class Limits:
    min_rub: int
    max_rub: int


def limits_for(user: User, biz: BizSettings) -> Limits:
    return Limits(
        min_rub=user.order_min_rub if user.order_min_rub is not None else biz.order_min_rub,
        max_rub=user.order_max_rub if user.order_max_rub is not None else biz.order_max_rub,
    )


async def active_order(session: AsyncSession, user_id: int) -> Order | None:
    stmt = select(Order).where(Order.user_id == user_id, Order.status.in_(ACTIVE_ORDER_STATUSES)).limit(1)
    result: Order | None = (await session.execute(stmt)).scalar_one_or_none()
    return result


async def get_for_user(session: AsyncSession, user_id: int, order_id: int) -> Order:
    order = await session.get(Order, order_id)
    if order is None or order.user_id != user_id:
        raise NotFound("Заявка не найдена")
    return order


async def _lock_order(session: AsyncSession, order_id: int) -> Order:
    stmt = select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
    order: Order | None = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise NotFound("Заявка не найдена")
    return order


async def check_can_create(
    session: AsyncSession, user: User, biz: BizSettings, now: datetime, amount_rub: int | None = None
) -> rates.CurrentRate:
    """All pre-checks from 6.3 p.1; repeated inside the creation transaction."""
    users_svc.ensure_active(user)
    if not biz.purchases_enabled:
        raise ServiceError("purchases_disabled", MSG_PURCHASES_OFF, status=403)
    if not user.can_create_orders:
        raise ServiceError("orders_blocked", MSG_ORDERS_BLOCKED, status=403)
    existing = await active_order(session, user.id)
    if existing is not None:
        session.expunge(existing)  # keep it usable after the transaction is rolled back
        raise ActiveOrderExists(existing)
    await antifraud.check_order_creation(session, user, biz, amount_rub, now)
    return await rates.require(session, biz, now)


def check_amount(user: User, biz: BizSettings, amount_rub: int) -> None:
    lim = limits_for(user, biz)
    if not lim.min_rub <= amount_rub <= lim.max_rub:
        raise ServiceError("amount_out_of_range", rub_range_message(lim.min_rub, lim.max_rub))


MSG_TOO_SMALL = "Сумма слишком мала: к зачислению получается 0 USDT. Увеличьте сумму."


def _positive(q: Quote) -> Quote:
    if q.usdt_amount <= 0:
        raise ServiceError("amount_too_small", MSG_TOO_SMALL)
    return q


@dataclass(frozen=True)
class StartInfo:
    limits: Limits
    rate: rates.CurrentRate


async def start(c: Container, user_id: int) -> StartInfo:
    """Checks before asking the amount."""
    async with c.db() as session:
        user = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        rate = await check_can_create(session, user, biz, c.clock.now())
        return StartInfo(limits_for(user, biz), rate)


async def preview(c: Container, user_id: int, amount_rub: int) -> Quote:
    async with c.db() as session:
        user = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        check_amount(user, biz, amount_rub)
        rate = await check_can_create(session, user, biz, c.clock.now(), amount_rub)
        return _positive(quote(amount_rub, user.markup_pct, rate.value))


@dataclass(frozen=True)
class CreateResult:
    order: Order
    created: bool


async def create(
    c: Container,
    user_id: int,
    amount_rub: int,
    *,
    source: OrderSource,
    idempotency_key: str | None,
    expected_usdt: Decimal | None = None,
    tg_chat_id: int | None = None,
) -> CreateResult:
    provider = c.payments.default
    now = c.clock.now()
    # 1. Transaction: lock user, repeat checks, insert `creating`.
    try:
        async with c.db.begin() as session:
            user = await users_svc.lock(session, user_id)
            if idempotency_key:
                existing = (
                    await session.execute(
                        select(Order).where(Order.user_id == user_id, Order.idempotency_key == idempotency_key)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return CreateResult(existing, False)
            biz = await settings_svc.load(session)
            check_amount(user, biz, amount_rub)
            rate = await check_can_create(session, user, biz, now, amount_rub)
            q = _positive(quote(amount_rub, user.markup_pct, rate.value))
            if expected_usdt is not None and q.usdt_amount != expected_usdt:
                raise PriceChanged(q)
            ttl = timedelta(minutes=biz.order_ttl_minutes)
            order = Order(
                public_id=f"tmp-{uuid.uuid4().hex}",
                user_id=user.id,
                amount_rub=amount_rub,
                rate=rate.value,
                rate_source=rate.source,
                markup_pct=user.markup_pct,
                usdt_amount=q.usdt_amount,
                margin_rub=q.margin_rub,
                status=OrderStatus.creating,
                provider=provider.name,
                source=source,
                idempotency_key=idempotency_key,
                tg_chat_id=tg_chat_id,
                created_at=now,
                expires_at=now + ttl,
            )
            session.add(order)
            await session.flush()
            order.public_id = f"B-{100000 + order.id}"
            await session.flush()
    except IntegrityError:
        # Lost a race on the "one active order" index or the idempotency key.
        async with c.db() as session:
            if idempotency_key:
                existing = (
                    await session.execute(
                        select(Order).where(Order.user_id == user_id, Order.idempotency_key == idempotency_key)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return CreateResult(existing, False)
            active = await active_order(session, user_id)
        if active is not None:
            raise ActiveOrderExists(active) from None
        raise

    # 2. Outside of any transaction: ask the provider for a QR.
    try:
        qr = await asyncio.wait_for(
            provider.create_qr(
                order_public_id=order.public_id,
                amount_rub=amount_rub,
                ttl_seconds=int(ttl.total_seconds()),
                description=f"Покупка USDT, заявка {order.public_id}",
            ),
            timeout=PROVIDER_TIMEOUT,
        )
    except Exception as exc:
        log.warning("provider_create_qr_failed", order=order.public_id, error=f"{type(exc).__name__}: {exc}")
        async with c.db.begin() as session:
            locked = await _lock_order(session, order.id)
            if locked.status == OrderStatus.creating:
                locked.status = order_transition(locked.status, OrderStatus.failed)
                locked.finished_at = c.clock.now()
            if c.throttle.allow("provider_failed", c.clock.now()):
                await notify.alert(session, f"❗ Платёжный провайдер не выдал QR: {type(exc).__name__}", now=now)
        return CreateResult(locked, True)

    # 3. Save the QR.
    async with c.db.begin() as session:
        locked = await _lock_order(session, order.id)
        if locked.status == OrderStatus.creating:
            locked.status = order_transition(locked.status, OrderStatus.pending)
            locked.provider_payment_id = qr.provider_payment_id
            locked.qr_url = qr.qr_url
            # never expire the order while the provider's QR can still be paid
            locked.expires_at = max(locked.expires_at, qr.expires_at)
        return CreateResult(locked, True)


async def attach_message(c: Container, order_id: int, chat_id: int, message_id: int) -> None:
    async with c.db.begin() as session:
        order = await _lock_order(session, order_id)
        order.tg_chat_id = chat_id
        order.tg_message_id = message_id


async def _close_qr_message(session: AsyncSession, order: Order, status_text: str, now: datetime) -> None:
    if order.tg_chat_id is None or order.tg_message_id is None:
        return
    await notify.enqueue(
        session,
        order.tg_chat_id,
        "editMessageCaption",
        {
            "message_id": order.tg_message_id,
            "caption": messages.caption_closed(order.public_id, status_text),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []},
        },
        now=now,
    )


async def _finished(session: AsyncSession, user: User, now: datetime) -> None:
    biz = await settings_svc.load(session)
    await antifraud.on_order_finished(session, user, biz, now)


async def cancel(c: Container, user_id: int, order_id: int) -> Order:
    now = c.clock.now()
    async with c.db.begin() as session:
        order = await _lock_order(session, order_id)
        if order.user_id != user_id:
            raise NotFound("Заявка не найдена")
        if order.status != OrderStatus.pending:
            raise ServiceError("cannot_cancel", "Эту заявку уже нельзя отменить.", status=409)
        user = await users_svc.lock(session, order.user_id)
        order.status = order_transition(order.status, OrderStatus.cancelled)
        order.finished_at = now
        await _close_qr_message(session, order, "Заявка отменена.", now)
        await _finished(session, user, now)
        return order


# --- payments ------------------------------------------------------------------------


async def _credit(session: AsyncSession, order: Order, user: User, amount: Decimal, now: datetime) -> None:
    await ledger.deposit(session, user, amount, order_id=order.id, now=now)
    order.status = order_transition(order.status, OrderStatus.paid)
    order.credited_usdt = amount
    order.paid_at = order.paid_at or now
    order.finished_at = now
    await notify.message(session, user.tg_id, messages.order_paid(order.public_id, amount, user.balance), now=now)
    await _close_qr_message(session, order, f"✅ Оплачено, зачислено {amount} USDT.", now)
    await _finished(session, user, now)


async def apply_event(session: AsyncSession, order: Order, event: CallbackEvent, now: datetime) -> str:
    """Apply a provider event to a locked order. Lock order: order first, then user."""
    user = await users_svc.lock(session, order.user_id)
    if event.status != "paid":
        if order.status == OrderStatus.pending:
            order.status = order_transition(order.status, OrderStatus.expired)
            order.finished_at = now
            await notify.message(session, user.tg_id, messages.order_expired(order.public_id), now=now)
            await _close_qr_message(session, order, "⌛ Заявка истекла.", now)
            await _finished(session, user, now)
            return f"provider_{event.status}"
        return "ignored"

    if order.status == OrderStatus.paid:
        return "already_paid"
    if order.status not in (OrderStatus.pending, OrderStatus.expired, OrderStatus.cancelled):
        await notify.alert(
            session,
            f"⚠️ Оплата по заявке {order.public_id} в статусе {order.status.value} — проверьте вручную.",
            now=now,
        )
        return f"ignored_{order.status.value}"

    order.paid_amount_rub = event.amount_rub
    order.payer_info = event.payer_info
    order.paid_at = now
    if order.status != OrderStatus.pending:
        reason: str | None = "paid_after_close"
    elif event.amount_rub is None:
        reason = "amount_unknown"  # the provider did not confirm the amount: never credit blindly
    elif event.amount_rub != order.amount_rub:
        reason = "amount_mismatch"
    elif order.usdt_amount <= 0:
        reason = "zero_amount"
    else:
        reason = None
    if reason is not None:
        was = order.status
        order.status = order_transition(order.status, OrderStatus.review)
        order.review_reason = reason
        order.finished_at = None
        await notify.alert(
            session,
            f"⚠️ Заявка {order.public_id} отправлена на разбор ({reason}): "
            f"ожидалось {order.amount_rub} ₽, пришло {event.amount_rub} ₽, статус был {was.value}.",
            now=now,
        )
        await notify.message(session, user.tg_id, messages.order_review(order.public_id), now=now)
        if was == OrderStatus.pending:
            await _close_qr_message(session, order, "ℹ️ Платёж получен, идёт проверка.", now)
        return reason

    await _credit(session, order, user, order.usdt_amount, now)
    return "credited"


async def _log_callback(
    c: Container,
    provider: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    signature_valid: bool | None,
    result: str,
) -> None:
    async with c.db.begin() as session:
        session.add(
            PaymentCallback(
                provider=provider,
                event_key=None,
                headers=_safe_headers(headers),
                body=body.decode("utf-8", errors="replace"),
                signature_valid=signature_valid,
                processed=False,
                result=result,
                created_at=c.clock.now(),
            )
        )


def _safe_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    return {k: v for k, v in headers.items() if k.lower() not in ("authorization", "cookie")}


async def process_event(
    c: Container, provider_name: str, event: CallbackEvent, headers: Mapping[str, str], body: bytes
) -> str:
    now = c.clock.now()
    async with c.db.begin() as session:
        stmt = (
            insert(PaymentCallback)
            .values(
                provider=provider_name,
                event_key=f"{provider_name}:{event.event_key}",
                headers=_safe_headers(headers),
                body=body.decode("utf-8", errors="replace"),
                signature_valid=True,
                processed=True,
                result="processing",
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[PaymentCallback.event_key])
            .returning(PaymentCallback.id)
        )
        callback_id = (await session.execute(stmt)).scalar_one_or_none()
        if callback_id is None:
            result = "duplicate"
        else:
            order = (
                await session.execute(
                    select(Order)
                    .where(Order.provider == provider_name, Order.provider_payment_id == event.provider_payment_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if order is None:
                result = "unknown_payment"
                await notify.alert(
                    session,
                    f"⚠️ Callback по неизвестному платежу {event.provider_payment_id} ({provider_name}).",
                    now=now,
                )
            else:
                result = await apply_event(session, order, event, now)
            cb = await session.get(PaymentCallback, callback_id)
            if cb is not None:
                cb.result = result
    if result == "duplicate":
        await _log_callback(c, provider_name, headers, body, signature_valid=True, result="duplicate")
    log.info("payment_event", provider=provider_name, event_key=event.event_key, result=result)
    return result


@dataclass(frozen=True)
class CallbackResponse:
    status: int
    result: str


async def handle_callback(
    c: Container, provider_name: str, headers: Mapping[str, str], body: bytes
) -> CallbackResponse:
    try:
        provider = c.payments.get(provider_name)
    except KeyError:
        await _log_callback(c, provider_name[:50], headers, body, signature_valid=None, result="unknown_provider")
        return CallbackResponse(404, "unknown_provider")
    try:
        valid = provider.verify_callback(headers, body)
    except Exception:
        log.exception("payment_callback_verify_failed", provider=provider_name)
        valid = False
    if not valid:
        await _log_callback(c, provider_name, headers, body, signature_valid=False, result="bad_signature")
        return CallbackResponse(401, "bad_signature")
    try:
        event = provider.parse_callback(body)
    except Exception as exc:
        log.warning("payment_callback_unparsable", provider=provider_name, error=str(exc))
        await _log_callback(c, provider_name, headers, body, signature_valid=True, result="parse_error")
        return CallbackResponse(400, "parse_error")
    try:
        result = await process_event(c, provider_name, event, headers, body)
    except Exception:
        log.exception("payment_callback_failed", provider=provider_name)
        try:
            await _log_callback(c, provider_name, headers, body, signature_valid=True, result="error")
        except Exception:
            log.exception("payment_callback_log_failed")
        return CallbackResponse(500, "error")
    return CallbackResponse(200, result)


# --- background ----------------------------------------------------------------------


async def expire_due(c: Container, batch: int = 100) -> int:
    """Reconcile with the provider, then expire overdue pending orders."""
    now = c.clock.now()
    async with c.db() as session:
        rows = (
            await session.execute(
                select(Order.id, Order.provider, Order.provider_payment_id, Order.expires_at)
                .where(Order.status == OrderStatus.pending, Order.expires_at <= now)
                .order_by(Order.expires_at)
                .limit(batch)
            )
        ).all()
    expired = 0
    for order_id, provider_name, payment_id, expires_at in rows:
        try:
            expired += await _expire_one(c, order_id, provider_name, payment_id, expires_at)
        except Exception:
            # one broken order must not block expiry and reconciliation of all the others
            log.exception("expire_order_failed", order_id=order_id)
            if c.throttle.allow(f"expire_failed:{order_id}", c.clock.now(), timedelta(hours=1)):
                async with c.db.begin() as session:
                    await notify.alert(session, f"❗ Не удалось закрыть просроченную заявку id {order_id}.", now=now)
    return expired


async def _expire_one(
    c: Container, order_id: int, provider_name: str, payment_id: str | None, expires_at: datetime
) -> int:
    now = c.clock.now()
    event: CallbackEvent | None = None
    provider: PaymentProvider | None = None
    try:
        provider = c.payments.get(provider_name)
        if payment_id:
            event = await asyncio.wait_for(provider.get_status(payment_id), timeout=PROVIDER_TIMEOUT)
    except Exception as exc:
        log.warning("provider_get_status_failed", order_id=order_id, error=f"{type(exc).__name__}: {exc}")
        if c.throttle.allow("provider_status_failed", now):
            async with c.db.begin() as session:
                await notify.alert(
                    session, f"❗ Платёжный провайдер недоступен для сверки: {type(exc).__name__}", now=now
                )
        if now < expires_at + RECONCILE_GRACE:
            return 0  # retry the reconciliation on the next run
    if event is not None and event.status == "paid":
        body = json.dumps({"source": "reconcile", **event.raw}, default=str).encode()
        await process_event(c, provider_name, event, {"x-source": "reconcile"}, body)
        async with c.db.begin() as session:
            order = await _lock_order(session, order_id)
            if order.status == OrderStatus.pending:
                # the provider says "paid" but the event could not be applied: let an admin decide
                await users_svc.lock(session, order.user_id)
                order.status = order_transition(order.status, OrderStatus.review)
                order.review_reason = "reconcile_unapplied"
                await notify.alert(
                    session, f"⚠️ Заявка {order.public_id}: провайдер сообщает об оплате, нужен разбор.", now=now
                )
        return 0
    async with c.db.begin() as session:
        order = await _lock_order(session, order_id)
        if order.status != OrderStatus.pending:
            return 0
        user = await users_svc.lock(session, order.user_id)
        order.status = order_transition(order.status, OrderStatus.expired)
        order.finished_at = c.clock.now()
        await notify.message(session, user.tg_id, messages.order_expired(order.public_id), now=now)
        await _close_qr_message(session, order, "⌛ Заявка истекла.", now)
        await _finished(session, user, now)
        return 1


async def fail_stuck_creating(c: Container) -> int:
    now = c.clock.now()
    count = 0
    async with c.db.begin() as session:
        stuck = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.status == OrderStatus.creating, Order.created_at <= now - STUCK_CREATING_AFTER)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for order in stuck:
            order.status = order_transition(order.status, OrderStatus.failed)
            order.finished_at = now
            count += 1
    return count


# --- review --------------------------------------------------------------------------

ReviewDecision = Literal["credit_order_rate", "credit_current_rate", "reject"]


async def resolve_review(
    c: Container,
    order_id: int,
    decision: ReviewDecision,
    *,
    staff_id: int,
    comment: str | None,
    ip: str | None = None,
) -> Order:
    now = c.clock.now()
    async with c.db.begin() as session:
        order = await _lock_order(session, order_id)
        if order.status != OrderStatus.review:
            raise ServiceError("not_in_review", "Заявка не находится на разборе.", status=409)
        user = await users_svc.lock(session, order.user_id)
        before = {"status": order.status.value, "review_reason": order.review_reason}
        paid_rub = order.paid_amount_rub if order.paid_amount_rub is not None else order.amount_rub
        if decision == "reject":
            if not comment or len(comment.strip()) < 3:
                raise ServiceError("comment_required", "Укажите причину отклонения.")
            order.status = order_transition(order.status, OrderStatus.rejected)
            order.finished_at = now
            await notify.message(session, user.tg_id, messages.order_rejected(order.public_id, comment), now=now)
            await _finished(session, user, now)
        else:
            if decision == "credit_order_rate":
                amount = (
                    order.usdt_amount
                    if paid_rub == order.amount_rub
                    else quote(paid_rub, order.markup_pct, order.rate).usdt_amount
                )
            else:
                biz = await settings_svc.load(session)
                rate = await rates.require(session, biz, now)
                amount = quote(paid_rub, order.markup_pct, rate.value).usdt_amount
            if amount <= 0:
                raise ServiceError("nothing_to_credit", "Сумма к зачислению равна нулю.")
            if paid_rub != order.amount_rub:
                order.margin_rub = quote(paid_rub, order.markup_pct, order.rate).margin_rub
            await _credit(session, order, user, amount, now)
        await audit.write(
            session,
            action=f"order.review.{decision}",
            staff_id=staff_id,
            entity_type="order",
            entity_id=order.id,
            before=before,
            after={"status": order.status.value, "credited_usdt": str(order.credited_usdt or "")},
            comment=comment,
            ip=ip,
        )
        return order


async def simulate_payment(
    c: Container, order_id: int, *, amount_rub: int | None, staff_id: int, ip: str | None = None
) -> CallbackResponse:
    """Mock only: build a real signed callback and send it through the common handler."""
    from app.payments.mock import MockProvider

    async with c.db() as session:
        order = await session.get(Order, order_id)
        if order is None:
            raise NotFound("Заявка не найдена")
    provider = c.payments.get(order.provider) if order.provider in c.payments.names() else None
    if not isinstance(provider, MockProvider):
        raise Forbidden("Симуляция доступна только для mock-провайдера", code="not_mock")
    if order.provider_payment_id is None:
        raise ServiceError("no_payment", "У заявки нет платежа.")
    paid = order.amount_rub if amount_rub is None else amount_rub
    await provider.mark(order.provider_payment_id, "paid", paid)
    headers, body = provider.build_callback(order.provider_payment_id, "paid", paid)
    async with c.db.begin() as session:
        await audit.write(
            session,
            action="order.simulate_payment",
            staff_id=staff_id,
            entity_type="order",
            entity_id=order.id,
            after={"amount_rub": paid},
            ip=ip,
        )
    return await handle_callback(c, provider.name, headers, body)
