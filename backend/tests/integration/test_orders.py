from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domain.enums import LedgerType, OrderSource, OrderStatus
from app.domain.errors import ServiceError
from app.models import LedgerEntry, Order, PaymentCallback, User
from app.services import ledger, orders, rates
from app.services import settings as settings_svc
from tests.conftest import make_staff, make_user, outbox, pay, reload, seed_rate


async def create(container, user, amount=1000, key=None, **kw):
    return await orders.create(container, user.id, amount, source=OrderSource.bot, idempotency_key=key, **kw)


async def deposits(container, user_id) -> list[Decimal]:
    async with container.db() as s:
        rows = await s.execute(
            select(LedgerEntry.balance_delta).where(
                LedgerEntry.user_id == user_id, LedgerEntry.type == LedgerType.deposit
            )
        )
        return [r[0] for r in rows]


@pytest.fixture
async def user(container):
    await seed_rate(container, "100")
    return await make_user(container)


async def test_create_success(container, user, clock) -> None:
    result = await create(container, user, 1000, key="k1")
    order = result.order
    assert result.created
    assert order.status == OrderStatus.pending
    assert re.fullmatch(r"B-\d{6}", order.public_id)
    assert order.usdt_amount == Decimal("9.50")
    assert order.margin_rub == Decimal("50.00")
    assert order.rate == Decimal(100) and order.rate_source == "rapira"
    assert order.expires_at == clock.now() + timedelta(minutes=15)
    assert re.fullmatch(
        r"https://qr\.nspk\.ru/MOCK[0-9A-F]{32}\?type=02&bank=100000000000&sum=100000&cur=RUB&crc=0000", order.qr_url
    )


async def test_active_order_and_idempotency(container, user) -> None:
    first = (await create(container, user, 1000, key="k1")).order
    again = await create(container, user, 5000, key="k1")
    assert not again.created and again.order.id == first.id
    with pytest.raises(orders.ActiveOrderExists) as exc:
        await create(container, user, 1000, key="k2")
    assert exc.value.order.id == first.id


async def test_parallel_creation_one_active(container, user) -> None:
    results = await asyncio.gather(
        create(container, user, 1000, key="a"), create(container, user, 1000, key="b"), return_exceptions=True
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 1 and len(errors) == 1 and isinstance(errors[0], orders.ActiveOrderExists)


async def test_double_confirm_same_key_parallel(container, user) -> None:
    results = await asyncio.gather(create(container, user, 1000, key="same"), create(container, user, 1000, key="same"))
    assert {r.order.id for r in results} == {results[0].order.id}
    assert sorted(r.created for r in results) == [False, True]


async def test_provider_error_marks_failed(container, user) -> None:
    container.payments.get("mock").fail_next_create = TimeoutError()
    result = await create(container, user, 1000, key="k1")
    assert result.order.status == OrderStatus.failed
    assert any("не выдал QR" in m["text"] for m in await outbox(container, 0))
    # the next attempt works
    assert (await create(container, user, 1000, key="k2")).order.status == OrderStatus.pending


async def test_stale_rate_refused(container, clock) -> None:
    await seed_rate(container, "100", at=clock.now() - timedelta(seconds=181))
    user = await make_user(container)
    with pytest.raises(rates.RateUnavailable):
        await create(container, user, 1000)


async def test_blocked_disabled_limits(container, user, clock) -> None:
    async with container.db.begin() as s:
        u = await s.get(User, user.id)
        u.can_create_orders = False
    with pytest.raises(ServiceError) as exc:
        await create(container, user)
    assert exc.value.code == "orders_blocked"
    assert exc.value.message == "Создание заявок приостановлено. Обратитесь в поддержку."

    async with container.db.begin() as s:
        u = await s.get(User, user.id)
        u.can_create_orders = True
        u.order_min_rub, u.order_max_rub = 2000, 3000
    with pytest.raises(ServiceError) as exc:
        await create(container, user, 1000)
    assert exc.value.code == "amount_out_of_range" and "от 2 000 до 3 000" in exc.value.message
    assert (await create(container, user, 2500)).order.status == OrderStatus.pending

    async with container.db.begin() as s:
        await settings_svc.update(s, {"purchases_enabled": False}, staff_id=None, now=clock.now())
    with pytest.raises(ServiceError) as exc:
        await orders.start(container, user.id)
    assert exc.value.code == "purchases_disabled"


async def test_price_changed(container, user) -> None:
    with pytest.raises(orders.PriceChanged) as exc:
        await create(container, user, 1000, expected_usdt=Decimal("9.60"))
    assert exc.value.quote.usdt_amount == Decimal("9.50")
    assert (await create(container, user, 1000, expected_usdt=Decimal("9.50"))).created


async def test_markup_change_applies_to_new_orders_only(container, user) -> None:
    first = (await create(container, user, 1000)).order
    async with container.db.begin() as s:
        (await s.get(User, user.id)).markup_pct = Decimal("2")
    await orders.cancel(container, user.id, first.id)
    second = (await create(container, user, 1000)).order
    assert (await reload(container, Order, first.id)).usdt_amount == Decimal("9.50")
    assert second.usdt_amount == Decimal("9.80")


# --- callbacks ---------------------------------------------------------------------------


async def test_callback_credits_once(container, user) -> None:
    order = (await create(container, user)).order
    await orders.attach_message(container, order.id, user.tg_id, 555)
    order = await reload(container, Order, order.id)
    resp, headers, body = await pay(container, order)
    assert (resp.status, resp.result) == (200, "credited")
    again = await orders.handle_callback(container, "mock", headers, body)
    assert (again.status, again.result) == (200, "duplicate")
    order = await reload(container, Order, order.id)
    assert order.status == OrderStatus.paid and order.credited_usdt == Decimal("9.50") and order.paid_at
    assert (await reload(container, User, user.id)).balance == Decimal("9.50")
    assert await deposits(container, user.id) == [Decimal("9.50")]
    msgs = await outbox(container, user.tg_id)
    assert msgs[0]["text"].startswith("✅ Оплата по заявке") and "Баланс: <b>9.50 USDT</b>" in msgs[0]["text"]
    assert msgs[1]["method"] == "editMessageCaption" and msgs[1]["reply_markup"] == {"inline_keyboard": []}
    async with container.db() as s:
        assert (await s.execute(select(func.count(PaymentCallback.id)))).scalar() == 2


async def test_parallel_identical_callbacks(container, user) -> None:
    order = (await create(container, user)).order
    provider = container.payments.get("mock")
    headers, body = provider.build_callback(order.provider_payment_id, "paid", 1000)
    results = await asyncio.gather(*[orders.handle_callback(container, "mock", headers, body) for _ in range(5)])
    assert sorted(r.result for r in results) == ["credited"] + ["duplicate"] * 4
    assert await deposits(container, user.id) == [Decimal("9.50")]


async def test_parallel_distinct_paid_events(container, user) -> None:
    order = (await create(container, user)).order
    provider = container.payments.get("mock")
    cbs = [provider.build_callback(order.provider_payment_id, "paid", 1000) for _ in range(5)]
    results = await asyncio.gather(*[orders.handle_callback(container, "mock", h, b) for h, b in cbs])
    assert sorted(r.result for r in results) == ["already_paid"] * 4 + ["credited"]
    assert (await reload(container, User, user.id)).balance == Decimal("9.50")


async def test_bad_signature(container, user) -> None:
    order = (await create(container, user)).order
    provider = container.payments.get("mock")
    _headers, body = provider.build_callback(order.provider_payment_id, "paid", 1000)
    resp = await orders.handle_callback(container, "mock", {"X-Signature": "00" * 32}, body)
    assert resp.status == 401
    resp = await orders.handle_callback(container, "mock", {}, body)
    assert resp.status == 401
    assert (await reload(container, Order, order.id)).status == OrderStatus.pending
    async with container.db() as s:
        rows = (await s.execute(select(PaymentCallback))).scalars().all()
    assert [r.signature_valid for r in rows] == [False, False] and rows[0].body == body.decode()


async def test_unknown_payment_and_provider_and_parse(container, user) -> None:
    provider = container.payments.get("mock")
    headers, body = provider.build_callback("MOCKNOPE", "paid", 1000)
    resp = await orders.handle_callback(container, "mock", headers, body)
    assert (resp.status, resp.result) == (200, "unknown_payment")
    assert any("неизвестному платежу" in m["text"] for m in await outbox(container, 0))
    assert (await orders.handle_callback(container, "nope", headers, body)).status == 404
    bad = b'{"event_id": "1"}'
    assert (await orders.handle_callback(container, "mock", {"X-Signature": provider.sign(bad)}, bad)).status == 400
    bad = b'{"event_id": "1", "payment_id": "x", "status": "weird"}'
    assert (await orders.handle_callback(container, "mock", {"X-Signature": provider.sign(bad)}, bad)).status == 400
    bad = b'{"event_id": "1", "payment_id": "x", "status": "paid", "amount_rub": "10"}'
    assert (await orders.handle_callback(container, "mock", {"X-Signature": provider.sign(bad)}, bad)).status == 400


async def test_amount_mismatch_goes_to_review(container, user) -> None:
    order = (await create(container, user)).order
    resp, _, _ = await pay(container, order, amount_rub=999)
    assert resp.result == "amount_mismatch"
    order = await reload(container, Order, order.id)
    assert order.status == OrderStatus.review and order.review_reason == "amount_mismatch"
    assert order.paid_amount_rub == 999
    assert (await reload(container, User, user.id)).balance == 0
    assert any("на разбор" in m["text"] for m in await outbox(container, 0))


@pytest.mark.parametrize("close", ["expire", "cancel"])
async def test_paid_after_close_goes_to_review(container, user, clock, close) -> None:
    order = (await create(container, user)).order
    if close == "cancel":
        await orders.cancel(container, user.id, order.id)
    else:
        clock.advance(minutes=16)
        assert await orders.expire_due(container) == 1
    resp, _, _ = await pay(container, order)
    assert resp.result == "paid_after_close"
    order = await reload(container, Order, order.id)
    assert order.status == OrderStatus.review
    assert (await reload(container, User, user.id)).balance == 0
    assert await deposits(container, user.id) == []


async def test_provider_failed_event_expires(container, user) -> None:
    order = (await create(container, user)).order
    resp, _, _ = await pay(container, order, status="failed")
    assert resp.result == "provider_failed"
    assert (await reload(container, Order, order.id)).status == OrderStatus.expired
    resp, _, _ = await pay(container, order, status="expired")
    assert resp.result == "ignored"


async def test_internal_error_returns_500_and_retry_works(container, user, monkeypatch) -> None:
    order = (await create(container, user)).order
    provider = container.payments.get("mock")
    headers, body = provider.build_callback(order.provider_payment_id, "paid", 1000)
    original = orders.apply_event

    async def broken(*a, **kw):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(orders, "apply_event", broken)
    assert (await orders.handle_callback(container, "mock", headers, body)).status == 500
    monkeypatch.setattr(orders, "apply_event", original)
    resp = await orders.handle_callback(container, "mock", headers, body)
    assert (resp.status, resp.result) == (200, "credited")


async def test_callback_endpoint(container, user, config) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    order = (await create(container, user)).order
    headers, body = container.payments.get("mock").build_callback(order.provider_payment_id, "paid", 1000)
    app = create_app(config, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        bad = await client.post("/api/payments/callback/mock", content=body, headers={"X-Signature": "x"})
        assert bad.status_code == 401 and bad.json()["error"]["code"] == "bad_signature"
        ok = await client.post("/api/payments/callback/mock", content=body, headers=headers)
        assert ok.status_code == 200 and ok.json() == {"ok": True, "result": "credited"}
        nf = await client.post("/api/payments/callback/other", content=body, headers=headers)
        assert nf.status_code == 404


# --- expiry ----------------------------------------------------------------------------


async def test_expire_and_reconcile(container, user, clock) -> None:
    order = (await create(container, user)).order
    await orders.attach_message(container, order.id, user.tg_id, 77)
    clock.advance(minutes=14)
    assert await orders.expire_due(container) == 0
    clock.advance(minutes=1)
    assert await orders.expire_due(container) == 1
    order = await reload(container, Order, order.id)
    assert order.status == OrderStatus.expired and order.finished_at == clock.now()
    msgs = await outbox(container, user.tg_id)
    assert "истекла" in msgs[0]["text"] and msgs[1]["method"] == "editMessageCaption"

    # paid but callback lost -> reconciliation credits it
    await seed_rate(container, "100")
    second = (await create(container, user)).order
    await container.payments.get("mock").mark(second.provider_payment_id, "paid", 1000)
    clock.advance(minutes=16)
    assert await orders.expire_due(container) == 0
    second = await reload(container, Order, second.id)
    assert second.status == OrderStatus.paid
    assert (await reload(container, User, user.id)).balance == Decimal("9.50")
    # a late real callback after reconciliation does not credit twice
    resp, _, _ = await pay(container, second)
    assert resp.result == "already_paid"


async def test_reconcile_provider_down(container, user, clock, monkeypatch) -> None:
    order = (await create(container, user)).order
    provider = container.payments.get("mock")

    async def down(_):
        raise ConnectionError("down")

    monkeypatch.setattr(provider, "get_status", down)
    clock.advance(minutes=16)
    assert await orders.expire_due(container) == 0
    assert (await reload(container, Order, order.id)).status == OrderStatus.pending
    clock.advance(minutes=10)
    assert await orders.expire_due(container) == 1


async def test_fail_stuck_creating(container, user, clock) -> None:
    async with container.db.begin() as s:
        s.add(
            Order(
                public_id="B-1",
                user_id=user.id,
                amount_rub=1000,
                rate=Decimal(100),
                rate_source="rapira",
                markup_pct=Decimal(5),
                usdt_amount=Decimal("9.5"),
                margin_rub=Decimal(50),
                status=OrderStatus.creating,
                provider="mock",
                source=OrderSource.bot,
                created_at=clock.now(),
                expires_at=clock.now() + timedelta(minutes=15),
            )
        )
    clock.advance(seconds=119)
    assert await orders.fail_stuck_creating(container) == 0
    clock.advance(seconds=2)
    assert await orders.fail_stuck_creating(container) == 1


# --- review -------------------------------------------------------------------------------


async def review_order(container, user, amount_rub=999):
    order = (await create(container, user)).order
    await pay(container, order, amount_rub=amount_rub)
    return order


async def test_review_credit_order_rate(container, user) -> None:
    staff = await make_staff(container)
    order = await review_order(container, user, 2000)
    order = await orders.resolve_review(container, order.id, "credit_order_rate", staff_id=staff.id, comment=None)
    assert order.status == OrderStatus.paid
    assert order.credited_usdt == Decimal("19.00")  # 2000 ₽ at the order's rate and markup
    assert (await reload(container, User, user.id)).balance == Decimal("19.00")
    with pytest.raises(ServiceError):
        await orders.resolve_review(container, order.id, "reject", staff_id=staff.id, comment="x")


async def test_review_credit_current_rate(container, user, clock) -> None:
    staff = await make_staff(container)
    order = await review_order(container, user, 1000)  # amounts equal but paid after close? -> mismatch path not hit
    order = await reload(container, Order, order.id)
    assert order.status == OrderStatus.paid  # equal amount -> credited directly
    order = await review_order(container, user, 500)
    clock.advance(seconds=1)
    await seed_rate(container, "50")
    order = await orders.resolve_review(
        container, order.id, "credit_current_rate", staff_id=staff.id, comment="by current"
    )
    assert order.credited_usdt == Decimal("9.50")  # 500 * 0.95 / 50


async def test_review_reject(container, user) -> None:
    staff = await make_staff(container)
    order = await review_order(container, user)
    with pytest.raises(ServiceError):
        await orders.resolve_review(container, order.id, "reject", staff_id=staff.id, comment="")
    order = await orders.resolve_review(container, order.id, "reject", staff_id=staff.id, comment="refund manually")
    assert order.status == OrderStatus.rejected
    assert (await reload(container, User, user.id)).balance == 0
    assert any("отклонена" in m["text"] for m in await outbox(container, user.tg_id))
    async with container.db() as s:
        assert await ledger.check_invariant(s) == []


async def test_simulate_payment(container, user) -> None:
    staff = await make_staff(container)
    order = (await create(container, user)).order
    resp = await orders.simulate_payment(container, order.id, amount_rub=None, staff_id=staff.id)
    assert resp.result == "credited"
    second = (await create(container, user)).order
    resp = await orders.simulate_payment(container, second.id, amount_rub=1500, staff_id=staff.id)
    assert resp.result == "amount_mismatch"


async def test_cancel_rules(container, user) -> None:
    order = (await create(container, user)).order
    other = await make_user(container, tg_id=101)
    with pytest.raises(ServiceError) as exc:
        await orders.cancel(container, other.id, order.id)
    assert exc.value.status == 404
    await orders.cancel(container, user.id, order.id)
    with pytest.raises(ServiceError) as exc:
        await orders.cancel(container, user.id, order.id)
    assert exc.value.code == "cannot_cancel"
