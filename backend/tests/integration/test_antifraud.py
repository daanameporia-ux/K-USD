from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.enums import AfSeverity, Network, OrderSource, OrderStatus, StaffRole
from app.domain.errors import ServiceError
from app.models import AddressBlacklist, AntifraudEvent, AuditLog, Order, User
from app.services import antifraud, orders, withdrawals
from app.services import settings as settings_svc
from app.services.actor import Actor
from tests.chain_fixtures import BSC_TO, TRON_TO
from tests.conftest import fund, make_staff, make_user, outbox, reload, seed_rate

S = OrderStatus


async def set_biz(container, **changes) -> None:
    async with container.db.begin() as s:
        await settings_svc.update(s, changes, staff_id=None, now=container.clock.now())


async def add_order(container, user, status, *, ago_min=1, amount=1000, payer=None) -> Order:
    now = container.clock.now()
    at = now - timedelta(minutes=ago_min)
    async with container.db.begin() as s:
        o = Order(
            public_id=f"tmp{now.timestamp()}{ago_min}{status}{amount}{payer}",
            user_id=user.id,
            amount_rub=amount,
            rate=Decimal(100),
            rate_source="rapira",
            markup_pct=Decimal(5),
            usdt_amount=Decimal(amount) / 100,
            margin_rub=Decimal(amount) / 20,
            status=status,
            provider="mock",
            source=OrderSource.bot,
            created_at=at,
            expires_at=at + timedelta(minutes=15),
            finished_at=at if status not in (S.pending, S.creating, S.review) else None,
            paid_at=at if status == S.paid else None,
            payer_info=payer,
            credited_usdt=Decimal(amount) / 100 if status == S.paid else None,
        )
        s.add(o)
        await s.flush()
        o.public_id = f"B-{800000 + o.id}"
        return o


async def recalc(container, user) -> User:
    async with container.db.begin() as s:
        from app.services import users as users_svc

        u = await users_svc.lock(s, user.id)
        biz = await settings_svc.load(s)
        await antifraud.on_order_finished(s, u, biz, container.clock.now())
    return await reload(container, User, user.id)


async def events(container, user_id=None) -> list[AntifraudEvent]:
    async with container.db() as s:
        stmt = select(AntifraudEvent).order_by(AntifraudEvent.id)
        if user_id is not None:
            stmt = stmt.where(AntifraudEvent.user_id == user_id)
        return list((await s.execute(stmt)).scalars())


@pytest.fixture
async def admin(container):
    s = await make_staff(container, tg_id=3001, role="admin")
    return Actor(s.id, StaffRole.admin)


@pytest.fixture
async def user(container):
    await seed_rate(container, "100")
    return await make_user(container)


async def new_order(container, user, amount=1000):
    await seed_rate(container, "100")
    return (await orders.create(container, user.id, amount, source=OrderSource.webapp, idempotency_key=None)).order


async def test_end_to_end_block_and_unblock(container, user, admin, clock) -> None:
    for i in range(5):
        order = await new_order(container, user)
        await orders.cancel(container, user.id, order.id)
        u = await reload(container, User, user.id)
        assert u.is_suspicious == (i >= 2)
        assert u.can_create_orders == (i < 4)
        clock.advance(minutes=61)
    assert {e.rule for e in await events(container, user.id)} == {"A2", "A3"}
    texts = [m["text"] for m in await outbox(container, 0) if "Антифрод" in m["text"]]
    assert len(texts) == 2 and any("A3" in t for t in texts)
    with pytest.raises(ServiceError) as exc:
        await new_order(container, user)
    assert exc.value.message == "Создание заявок приостановлено. Обратитесь в поддержку."

    operator = Actor((await make_staff(container, tg_id=3002, role="operator")).id, StaffRole.operator)
    with pytest.raises(ServiceError):
        await antifraud.unblock(container, user.id, operator, "please")
    await antifraud.unblock(container, user.id, admin, "talked to the client")
    u = await reload(container, User, user.id)
    assert u.can_create_orders and not u.is_suspicious and u.af_reset_at == clock.now()
    assert all(e.resolved_at is not None for e in await events(container, user.id))
    order = await new_order(container, user)
    await orders.cancel(container, user.id, order.id)
    u = await reload(container, User, user.id)
    assert u.can_create_orders and not u.is_suspicious
    async with container.db() as s:
        audit = (await s.execute(select(AuditLog).where(AuditLog.action == "user.unblock_orders"))).scalar_one()
    assert audit.comment == "talked to the client"


async def test_repeated_trigger_not_duplicated(container, user) -> None:
    for _ in range(5):
        await add_order(container, user, S.expired)
    await recalc(container, user)
    await add_order(container, user, S.cancelled)
    await recalc(container, user)
    assert sorted(e.rule for e in await events(container, user.id)) == ["A2", "A3"]


async def test_a1_low_conversion_and_reset(container, user, clock) -> None:
    await set_biz(container, b3_enabled=False)
    for i in range(10):
        await add_order(container, user, S.paid if i % 10 < 7 else S.expired, ago_min=100 - i)
    # newest three are unpaid → A2 too; check A1 specifically
    u = await recalc(container, user)
    assert not u.can_create_orders
    assert "A1" in {e.rule for e in await events(container, user.id)}


async def test_a1_ignores_orders_before_reset(container, user, clock) -> None:
    for i in range(10):
        await add_order(container, user, S.paid if i < 7 else S.expired, ago_min=100 - i)
    async with container.db.begin() as s:
        (await s.get(User, user.id)).af_reset_at = clock.now()
    u = await recalc(container, user)
    assert u.can_create_orders and await events(container, user.id) == []


@pytest.mark.parametrize("scope", ["global", "user"])
async def test_antifraud_disabled(container, user, scope) -> None:
    if scope == "global":
        await set_biz(container, af_enabled=False)
    else:
        async with container.db.begin() as s:
            (await s.get(User, user.id)).af_enabled = False
    for _ in range(6):
        await add_order(container, user, S.expired)
    u = await recalc(container, user)
    assert u.can_create_orders and not u.is_suspicious
    assert await events(container, user.id) == []
    assert (await new_order(container, user)).status == S.pending  # B3 is off too


@pytest.mark.parametrize("enabled", [True, False])
async def test_b2_frequency(container, user, enabled) -> None:
    await set_biz(container, b2_enabled=enabled)
    for i in range(5):
        await add_order(container, user, S.paid, ago_min=50 - i)
    if enabled:
        with pytest.raises(ServiceError) as exc:
            await new_order(container, user)
        assert exc.value.code == "rate_limited" and "после" in exc.value.message
    else:
        assert (await new_order(container, user)).status == S.pending


@pytest.mark.parametrize("enabled", [True, False])
async def test_b3_cooldown(container, user, clock, enabled) -> None:
    await set_biz(container, b3_enabled=enabled)
    await add_order(container, user, S.expired, ago_min=3)
    await add_order(container, user, S.cancelled, ago_min=2)
    if enabled:
        with pytest.raises(ServiceError) as exc:
            await new_order(container, user)
        assert exc.value.code == "cooldown"
        clock.advance(minutes=3)
        assert (await new_order(container, user)).status == S.pending
    else:
        assert (await new_order(container, user)).status == S.pending


@pytest.mark.parametrize("enabled", [True, False])
async def test_b4_shared_address(container, enabled) -> None:
    await set_biz(container, b4_enabled=enabled)
    a = await make_user(container, tg_id=501)
    b = await make_user(container, tg_id=502)
    for u in (a, b):
        await fund(container, u, "100")
        await withdrawals.create(container, u.id, Network.TRC20, TRON_TO, Decimal(60), idempotency_key=None)
    ua, ub = await reload(container, User, a.id), await reload(container, User, b.id)
    assert (ua.is_suspicious, ub.is_suspicious) == (enabled, enabled)
    assert {e.rule for e in await events(container)} == ({"B4"} if enabled else set())


@pytest.mark.parametrize("enabled", [True, False])
async def test_b5_blacklist(container, enabled) -> None:
    await set_biz(container, b5_enabled=enabled)
    u = await make_user(container)
    await fund(container, u, "100")
    async with container.db.begin() as s:
        s.add(AddressBlacklist(network=Network.BEP20, address=BSC_TO.lower()))
    call = withdrawals.create(container, u.id, Network.BEP20, BSC_TO, Decimal(60), idempotency_key=None)
    if enabled:
        with pytest.raises(ServiceError) as exc:
            await call
        assert exc.value.code == "address_blacklisted"
    else:
        assert (await call).created


@pytest.mark.parametrize("enabled", [True, False])
async def test_b9_newcomer_limits(container, user, enabled) -> None:
    await set_biz(container, b9_enabled=enabled, b3_enabled=False, b2_enabled=False)
    if enabled:
        with pytest.raises(ServiceError) as exc:
            await new_order(container, user, 20001)
        assert exc.value.code == "newcomer_limit"
        await add_order(container, user, S.paid, amount=20000)
        await add_order(container, user, S.paid, amount=20000)
        with pytest.raises(ServiceError):
            await new_order(container, user, 10001)
        await add_order(container, user, S.paid, amount=1000)
        assert (await new_order(container, user, 30000)).status == S.pending
    else:
        assert (await new_order(container, user, 30000)).status == S.pending


@pytest.mark.parametrize("enabled", [True, False])
async def test_b10_daily_turnover(container, user, enabled) -> None:
    await set_biz(container, b10_enabled=enabled, b10_daily_rub=50000, b2_enabled=False)
    await add_order(container, user, S.paid, amount=40000)
    if enabled:
        with pytest.raises(ServiceError) as exc:
            await new_order(container, user, 10001)
        assert exc.value.code == "daily_limit"
        async with container.db.begin() as s:
            (await s.get(User, user.id)).daily_turnover_limit_rub = 60000
        assert (await new_order(container, user, 20000)).status == S.pending
    else:
        assert (await new_order(container, user, 50000)).status == S.pending


@pytest.mark.parametrize("enabled", [True, False])
async def test_b11_withdraw_hold(container, enabled) -> None:
    await set_biz(container, b11_enabled=enabled)
    u = await make_user(container)
    await fund(container, u, "100")
    call = withdrawals.create(container, u.id, Network.TRC20, TRON_TO, Decimal(60), idempotency_key=None)
    if enabled:
        with pytest.raises(ServiceError):
            await call
    else:
        assert (await call).created


@pytest.mark.parametrize("enabled", [True, False])
async def test_b12_fast_transit(container, enabled) -> None:
    await set_biz(container, b12_enabled=enabled)
    u = await make_user(container)
    await fund(container, u, "100")
    await withdrawals.create(container, u.id, Network.TRC20, TRON_TO, Decimal(95), idempotency_key=None)
    assert (await reload(container, User, u.id)).is_suspicious == enabled
    assert [e.rule for e in await events(container, u.id)] == (["B12"] if enabled else [])


@pytest.mark.parametrize("enabled", [True, False])
async def test_b13_many_payers(container, user, enabled) -> None:
    await set_biz(container, b13_enabled=enabled)
    for i in range(4):
        await add_order(container, user, S.paid, ago_min=10 + i, payer={"name": f"Payer {i}"})
    u = await recalc(container, user)
    assert u.is_suspicious == enabled
    # payer info arrives through the callback
    order = await new_order(container, user)
    provider = container.payments.get("mock")
    headers, body = provider.build_callback(order.provider_payment_id, "paid", 1000, payer={"phone": "+7900"})
    assert (await orders.handle_callback(container, "mock", headers, body)).result == "credited"
    assert (await reload(container, Order, order.id)).payer_info == {"phone": "+7900"}


@pytest.mark.parametrize("enabled", [True, False])
async def test_b14_daily_withdraw_limit(container, enabled) -> None:
    await set_biz(container, b14_enabled=enabled, b14_daily_usdt="100")
    u = await make_user(container)
    await fund(container, u, "300")
    await withdrawals.create(container, u.id, Network.TRC20, TRON_TO, Decimal(60), idempotency_key=None)
    call = withdrawals.create(container, u.id, Network.TRC20, TRON_TO, Decimal(60), idempotency_key=None)
    if enabled:
        with pytest.raises(ServiceError) as exc:
            await call
        assert exc.value.code == "daily_withdraw_limit"
    else:
        assert (await call).created


@pytest.mark.parametrize(("enabled", "auto_pause"), [(True, False), (True, True), (False, True)])
async def test_b8_global_conversion(container, user, enabled, auto_pause) -> None:
    await set_biz(container, b8_enabled=enabled, b8_auto_pause=auto_pause)
    for i in range(20):
        await add_order(container, user, S.paid if i < 7 else S.expired, ago_min=30)
    trigger = await antifraud.global_conversion_watch(container)
    async with container.db() as s:
        biz = await settings_svc.load(s)
    alerts = [m["text"] for m in await outbox(container, 0) if "B8" in m["text"]]
    if enabled:
        assert trigger is not None and len(alerts) == 1
        assert biz.purchases_enabled is (not auto_pause)
        assert (await antifraud.global_conversion_watch(container)) is not None
        assert len([m for m in await outbox(container, 0) if "B8" in m["text"]]) == 1  # throttled
        assert [e.severity for e in await events(container)] == [AfSeverity.info]
    else:
        assert trigger is None and alerts == [] and biz.purchases_enabled


async def test_block_orders_and_resolve_event(container, user, admin) -> None:
    await antifraud.block_orders(container, user.id, admin, "chargebacks")
    u = await reload(container, User, user.id)
    assert not u.can_create_orders and u.orders_block_reason == "Вручную: chargebacks"
    for _ in range(3):
        await add_order(container, user, S.expired)
    await recalc(container, user)
    (event,) = await events(container, user.id)
    resolved = await antifraud.resolve_event(container, event.id, admin)
    assert resolved.resolved_at is not None and resolved.resolved_by == admin.id
    with pytest.raises(ServiceError):
        await antifraud.resolve_event(container, 999999, admin)
    with pytest.raises(ServiceError):
        await antifraud.block_orders(container, user.id, admin, "")
