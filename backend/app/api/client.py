"""Client API for the Mini App. Calls the same service functions as the bot."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api import serializers as ser
from app.api.deps import MAX_ID, MONEY_LIMIT, ClientAuth, client_auth, container, idempotency_key, limit
from app.container import Container
from app.domain.amounts import AmountError, parse_rub, parse_usdt
from app.domain.enums import Network, OrderSource, OrderStatus
from app.domain.errors import NotFound, ServiceError
from app.domain.pricing import quote
from app.kyc.base import KycInput
from app.services import history, ledger, orders, rates, users, withdrawals
from app.services import settings as settings_svc
from app.services.labels import NETWORK
from app.services.qr import qr_png

Id = Annotated[int, Path(ge=1, le=MAX_ID)]

router = APIRouter(prefix="/api/client")


class AmountIn(BaseModel):
    amount_rub: str | int = Field(union_mode="left_to_right")


class OrderIn(AmountIn):
    expected_usdt: str | None = None


class WithdrawIn(BaseModel):
    network: Network
    address: str = Field(max_length=200)
    amount: str = Field(max_length=40)


class RegisterIn(BaseModel):
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def _parse_amount(value: str | int, lim: orders.Limits) -> int:
    try:
        return parse_rub(str(value), lim.min_rub, lim.max_rub)
    except AmountError as exc:
        raise ServiceError("invalid_amount", exc.message) from None


def _parse_usdt(value: str) -> Decimal:
    try:
        return parse_usdt(value)
    except AmountError as exc:
        raise ServiceError("invalid_amount", exc.message) from None


async def _me(c: Container, user_id: Id) -> dict[str, Any]:
    now = c.clock.now()
    async with c.db() as session:
        user = await users.get(session, user_id)
        biz = await settings_svc.load(session)
        info = await ledger.summary(session, user)
        available = await withdrawals.available_for_withdrawal(session, user, biz, now)
        active = await orders.active_order(session, user.id)
        lim = orders.limits_for(user, biz)
    return {
        "id": user.id,
        "tg_id": user.tg_id,
        "username": user.tg_username,
        "first_name": user.first_name,
        "status": user.status.value,
        "kyc_status": user.kyc_status.value,
        "registered": users.is_active(user),
        "blocked": user.status.value == "blocked",
        "balance": ser.money(info.available),
        "hold": ser.money(info.hold),
        "available_for_withdrawal": ser.money(available),
        "total_bought": ser.money(info.total_bought),
        "total_withdrawn": ser.money(info.total_withdrawn),
        "markup_pct": ser.num(user.markup_pct),
        "limits": {"min_rub": str(lim.min_rub), "max_rub": str(lim.max_rub)},
        "can_create_orders": user.can_create_orders,
        "orders_blocked_message": None if user.can_create_orders else orders.MSG_ORDERS_BLOCKED,
        "purchases_enabled": biz.purchases_enabled,
        "withdrawals_enabled": biz.withdrawals_enabled,
        "support_contact": biz.support_contact,
        "active_order": ser.order(active) if active is not None else None,
    }


@router.get("/me")
async def me(a: ClientAuth = Depends(client_auth), c: Container = Depends(container)) -> dict[str, Any]:
    return await _me(c, a.user.id)


@router.post("/register")
async def register(
    body: RegisterIn | None = None, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db.begin() as session:
        if body is not None and (body.text or body.data):
            step = await users.registration_input(
                session, c.kyc, a.user.id, KycInput(text=body.text, data=body.data), c.clock.now()
            )
        else:
            step = await users.start_registration(session, c.kyc, a.user.id, c.clock.now())
    return {
        "step": {"status": step.status, "message": step.message, "expects": step.expects},
        "me": await _me(c, a.user.id),
    }


@router.get("/rate")
async def get_rate(a: ClientAuth = Depends(client_auth), c: Container = Depends(container)) -> dict[str, Any]:
    async with c.db() as session:
        biz = await settings_svc.load(session)
        user = await users.get(session, a.user.id)
        cur = await rates.require(session, biz, c.clock.now())
    q = quote(1000, user.markup_pct, cur.value)
    return {
        "rate": ser.rate_str(cur.value),
        "source": cur.source,
        "age_seconds": cur.age_seconds,
        "markup_pct": ser.num(user.markup_pct),
        "effective_rate": ser.rate_str(q.effective_rate),
    }


@router.post("/orders/preview")
async def order_preview(
    body: AmountIn, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    info = await orders.start(c, a.user.id)
    amount = _parse_amount(body.amount_rub, info.limits)
    return ser.quote(await orders.preview(c, a.user.id, amount))


@router.post("/orders")
async def order_create(
    body: OrderIn,
    a: ClientAuth = Depends(client_auth),
    key: str = Depends(idempotency_key),
    c: Container = Depends(container),
) -> dict[str, Any]:
    limit(c, f"money:{a.user.id}", MONEY_LIMIT)
    async with c.db() as session:
        user = await users.get(session, a.user.id)
        biz = await settings_svc.load(session)
        lim = orders.limits_for(user, biz)
    amount = _parse_amount(body.amount_rub, lim)
    expected = None
    if body.expected_usdt is not None:
        try:
            expected = Decimal(body.expected_usdt)
        except InvalidOperation:
            raise ServiceError("invalid_amount", "Некорректная сумма") from None
    result = await orders.create(
        c, a.user.id, amount, source=OrderSource.webapp, idempotency_key=f"web:{key}", expected_usdt=expected
    )
    data = ser.order(result.order)
    data["created"] = result.created
    if result.order.status == OrderStatus.failed:
        data["error"] = {"code": "provider_failed", "message": orders.MSG_PROVIDER_FAILED}
    return data


@router.get("/orders/active")
async def order_active(a: ClientAuth = Depends(client_auth), c: Container = Depends(container)) -> dict[str, Any]:
    async with c.db() as session:
        active = await orders.active_order(session, a.user.id)
    return {"order": ser.order(active) if active else None}


@router.get("/orders/{order_id}")
async def order_get(
    order_id: Id, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        return ser.order(await orders.get_for_user(session, a.user.id, order_id))


@router.get("/orders/{order_id}/qr.png")
async def order_qr(order_id: Id, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)) -> Response:
    async with c.db() as session:
        o = await orders.get_for_user(session, a.user.id, order_id)
    if not o.qr_url:
        raise NotFound("QR-код недоступен")
    return Response(qr_png(o.qr_url), media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


@router.post("/orders/{order_id}/cancel")
async def order_cancel(
    order_id: Id, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    return ser.order(await orders.cancel(c, a.user.id, order_id))


@router.get("/withdrawals/config")
async def wd_config(a: ClientAuth = Depends(client_auth), c: Container = Depends(container)) -> dict[str, Any]:
    cfg = await withdrawals.get_config(c, a.user.id)
    return {
        "networks": [{"network": n.value, "label": NETWORK[n], "fee": ser.money(cfg.fees[n])} for n in Network],
        "min_amount": ser.money(cfg.min_amount),
        "available": ser.money(cfg.available),
        "enabled": cfg.enabled,
    }


@router.post("/withdrawals/preview")
async def wd_preview(
    body: WithdrawIn, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    pv = await withdrawals.preview(c, a.user.id, body.network, body.address, _parse_usdt(body.amount))
    return {
        "network": pv.network.value,
        "network_label": NETWORK[pv.network],
        "address": pv.address,
        "amount": ser.money(pv.amount),
        "fee": ser.money(pv.fee),
        "amount_to_send": ser.money(pv.amount_to_send),
    }


@router.post("/withdrawals")
async def wd_create(
    body: WithdrawIn,
    a: ClientAuth = Depends(client_auth),
    key: str = Depends(idempotency_key),
    c: Container = Depends(container),
) -> dict[str, Any]:
    limit(c, f"money:{a.user.id}", MONEY_LIMIT)
    result = await withdrawals.create(
        c, a.user.id, body.network, body.address, _parse_usdt(body.amount), idempotency_key=f"web:{key}"
    )
    data = ser.withdrawal(result.withdrawal)
    data["created"] = result.created
    return data


@router.post("/withdrawals/{withdrawal_id}/cancel")
async def wd_cancel(
    withdrawal_id: Id, a: ClientAuth = Depends(client_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    return ser.withdrawal(await withdrawals.cancel(c, a.user.id, withdrawal_id))


@router.get("/history")
async def get_history(
    cursor: str | None = Query(None, max_length=200),
    limit_: int = Query(20, alias="limit", ge=1, le=100),
    a: ClientAuth = Depends(client_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    async with c.db() as session:
        items, nxt = await history.feed(session, a.user.id, limit=limit_, cursor=cursor)
    return {"items": [ser.history_item(i) for i in items], "next_cursor": nxt}
