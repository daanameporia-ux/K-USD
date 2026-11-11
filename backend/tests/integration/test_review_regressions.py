"""Regression tests for defects found by the independent line-by-line review."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from app.domain.enums import Network, OrderSource, OrderStatus, StaffRole, VerifyStatus, WithdrawalStatus
from app.domain.errors import ServiceError
from app.domain.rates import RateParseError, parse_rapira
from app.jobs.outbox import send_notifications
from app.jobs.scheduler import Job, Scheduler
from app.models import AntifraudEvent, AuthSession, LoginAttempt, Order, PaymentCallback, Staff, User, Withdrawal
from app.services import antifraud, notify, orders, withdrawals
from app.services import settings as settings_svc
from app.services import staff as staff_svc
from app.services.actor import Actor
from tests.chain_fixtures import BSC_RPC1, BSC_TO, BSC_TX, TRON_AMOUNT, TRON_TO, TRON_TX, bsc_handler, load, mock_tron
from tests.conftest import fund, login_client, login_staff, make_staff, make_user, outbox, pay, reload, seed_rate


async def set_biz(container, **changes) -> None:
    async with container.db.begin() as s:
        await settings_svc.update(s, changes, staff_id=None, now=container.clock.now())


async def create_order(container, user, amount=1000):
    return (await orders.create(container, user.id, amount, source=OrderSource.bot, idempotency_key=None)).order


@pytest.fixture
async def user(container):
    await seed_rate(container, "100")
    return await make_user(container)


@pytest.fixture
async def operator(container):
    s = await make_staff(container, tg_id=7001, role="operator")
    return Actor(s.id, StaffRole.operator)


@pytest.fixture
async def admin(container):
    s = await make_staff(container, tg_id=7002, role="admin")
    return Actor(s.id, StaffRole.admin)


# --- purchases -------------------------------------------------------------------------


async def test_zero_usdt_order_is_refused(container, user) -> None:
    async with container.db.begin() as s:
        (await s.get(User, user.id)).markup_pct = Decimal("99.99")
    with pytest.raises(ServiceError) as exc:
        await orders.preview(container, user.id, 1000)
    assert exc.value.code == "amount_too_small"
    with pytest.raises(ServiceError):
        await create_order(container, user)


async def test_paid_callback_without_amount_goes_to_review(container, user) -> None:
    order = await create_order(container, user)
    provider = container.payments.get("mock")
    headers, body = provider.build_callback(order.provider_payment_id, "paid", None)
    resp = await orders.handle_callback(container, "mock", headers, body)
    assert resp.result == "amount_unknown"
    assert (await reload(container, Order, order.id)).status == OrderStatus.review
    assert (await reload(container, User, user.id)).balance == 0


async def test_expiry_continues_after_a_broken_order(container, user, clock, monkeypatch) -> None:
    first = await create_order(container, user)
    other = await make_user(container, tg_id=101)
    second = await create_order(container, other)
    clock.advance(minutes=16)
    real = orders._expire_one

    async def flaky(c, order_id, *a):
        if order_id == first.id:
            raise RuntimeError("broken order")
        return await real(c, order_id, *a)

    monkeypatch.setattr(orders, "_expire_one", flaky)
    assert await orders.expire_due(container) == 1
    assert (await reload(container, Order, second.id)).status == OrderStatus.expired
    assert any("Не удалось закрыть" in m["text"] for m in await outbox(container, 0))


async def test_reconcile_paid_but_not_applied_goes_to_review(container, user, clock) -> None:
    order = await create_order(container, user)
    provider = container.payments.get("mock")
    await provider.mark(order.provider_payment_id, "paid", 1000)
    event = await provider.get_status(order.provider_payment_id)
    # the same event key was already consumed (e.g. by an earlier unknown_payment): duplicate on reconcile
    async with container.db.begin() as s:
        s.add(
            PaymentCallback(
                provider="mock",
                event_key=f"mock:{event.event_key}",
                headers={},
                body="{}",
                signature_valid=True,
                processed=True,
                result="unknown_payment",
            )
        )
    clock.advance(minutes=16)
    await orders.expire_due(container)
    order = await reload(container, Order, order.id)
    assert order.status == OrderStatus.review and order.review_reason == "reconcile_unapplied"


async def test_event_keys_are_per_provider(container, user) -> None:
    order = await create_order(container, user)
    resp, _, _ = await pay(container, order)
    assert resp.result == "credited"
    async with container.db() as s:
        keys = [k for (k,) in await s.execute(select(PaymentCallback.event_key)) if k]
    assert keys and all(k.startswith("mock:") for k in keys)


async def test_unknown_provider_callback_is_stored(container) -> None:
    resp = await orders.handle_callback(container, "nope", {}, b'{"x": 1}')
    assert resp.status == 404
    async with container.db() as s:
        row = (await s.execute(select(PaymentCallback))).scalar_one()
    assert row.result == "unknown_provider" and row.body == '{"x": 1}'


async def test_mock_parser_rejects_bad_payer_and_negative_amount(container) -> None:
    provider = container.payments.get("mock")
    for doc in (
        {"event_id": "1", "payment_id": "p", "status": "paid", "amount_rub": -5},
        {"event_id": "1", "payment_id": "p", "status": "paid", "amount_rub": 5, "payer": "str"},
    ):
        body = json.dumps(doc).encode()
        assert (
            await orders.handle_callback(container, "mock", {"X-Signature": provider.sign(body)}, body)
        ).status == 400


async def test_review_margin_follows_paid_amount(container, user) -> None:
    staff = await make_staff(container)
    order = await create_order(container, user)
    await pay(container, order, amount_rub=5000)
    order = await orders.resolve_review(container, order.id, "credit_order_rate", staff_id=staff.id, comment=None)
    assert order.credited_usdt == Decimal("47.50") and order.margin_rub == Decimal("250.00")


async def test_review_reject_comment_is_escaped(container, user) -> None:
    staff = await make_staff(container)
    order = await create_order(container, user)
    await pay(container, order, amount_rub=999)
    await orders.resolve_review(container, order.id, "reject", staff_id=staff.id, comment="оплата < суммы & R&D")
    text = (await outbox(container, user.tg_id))[-1]["text"]
    assert "оплата &lt; суммы &amp; R&amp;D" in text


# --- withdrawals ------------------------------------------------------------------------


async def test_one_transfer_cannot_close_two_withdrawals(container, operator, admin, clock) -> None:
    await set_biz(container, withdraw_min_usdt="1")
    client = await make_user(container, tg_id=300)
    await fund(container, client, "100")
    amount = TRON_AMOUNT + 4
    w1 = (
        await withdrawals.create(container, client.id, Network.TRC20, TRON_TO, amount, idempotency_key=None)
    ).withdrawal
    w2 = (
        await withdrawals.create(container, client.id, Network.TRC20, TRON_TO, amount, idempotency_key=None)
    ).withdrawal
    await withdrawals.take(container, w1.id, operator)
    with respx.mock:
        mock_tron(respx.mock, "not_found")
        await withdrawals.submit_hash(container, w1.id, operator, TRON_TX)
        clock.advance(minutes=31)
        await withdrawals.verify_due(container)
    assert (await reload(container, Withdrawal, w1.id)).status == WithdrawalStatus.in_progress
    w1 = await withdrawals.force_complete(container, w1.id, admin, "seen on tronscan")
    assert w1.tx_hash == TRON_TX  # the last submitted hash is bound to the forced withdrawal
    await withdrawals.take(container, w2.id, operator)
    with pytest.raises(ServiceError) as exc:
        await withdrawals.submit_hash(container, w2.id, operator, TRON_TX)
    assert exc.value.code == "duplicate_hash"


async def test_force_complete_with_explicit_hash(container, admin) -> None:
    client = await make_user(container, tg_id=301)
    await fund(container, client, "100")
    w = (
        await withdrawals.create(container, client.id, Network.BEP20, BSC_TO, Decimal(60), idempotency_key=None)
    ).withdrawal
    await withdrawals.take(container, w.id, admin)
    w = await withdrawals.force_complete(container, w.id, admin, "manual send", tx_hash=BSC_TX.upper())
    assert w.tx_hash == BSC_TX
    msg = (await outbox(container, client.tg_id))[-1]["text"]
    assert "bscscan.com/tx/" in msg


async def test_reject_reason_is_escaped(container, operator) -> None:
    client = await make_user(container, tg_id=302)
    await fund(container, client, "100")
    w = (
        await withdrawals.create(container, client.id, Network.TRC20, TRON_TO, Decimal(60), idempotency_key=None)
    ).withdrawal
    await withdrawals.reject(container, w.id, operator, "сумма <50 USDT & AML")
    assert "сумма &lt;50 USDT &amp; AML" in (await outbox(container, client.tg_id))[-1]["text"]


@pytest.mark.parametrize("mutation", ["no_receipt_result", "no_block_time"])
async def test_tron_incomplete_answer_is_not_a_pass(container, operator, clock, mutation) -> None:
    await set_biz(container, withdraw_min_usdt="1")
    client = await make_user(container, tg_id=303)
    await fund(container, client, "100")
    w = (
        await withdrawals.create(container, client.id, Network.TRC20, TRON_TO, TRON_AMOUNT + 4, idempotency_key=None)
    ).withdrawal
    await withdrawals.take(container, w.id, operator)
    info, events = load("tron_txinfo_ok.json"), load("tron_events_ok.json")
    if mutation == "no_receipt_result":
        del info["receipt"]["result"]
    else:
        del info["blockTimeStamp"]
    with respx.mock:
        respx.post("https://api.trongrid.io/walletsolidity/gettransactioninfobyid").mock(
            return_value=httpx.Response(200, json=info)
        )
        respx.get(f"https://api.trongrid.io/v1/transactions/{TRON_TX}/events").mock(
            return_value=httpx.Response(200, json=events)
        )
        result = await withdrawals.submit_hash(container, w.id, operator, TRON_TX)
    expected = VerifyStatus.tx_failed if mutation == "no_receipt_result" else VerifyStatus.explorer_unavailable
    assert result.verify_status == expected


async def test_bsc_malformed_answer_is_recorded(container, operator) -> None:
    await set_biz(container, withdraw_min_usdt="1")
    client = await make_user(container, tg_id=304)
    await fund(container, client, "100")
    w = (
        await withdrawals.create(container, client.id, Network.BEP20, BSC_TO, Decimal("41.13"), idempotency_key=None)
    ).withdrawal
    await withdrawals.take(container, w.id, operator)
    good = bsc_handler("ok")

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["method"] == "eth_blockNumber":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})
        return good(request)

    with respx.mock:
        respx.post(BSC_RPC1).mock(side_effect=handler)
        result = await withdrawals.submit_hash(container, w.id, operator, BSC_TX)
    w = result.withdrawal
    assert result.verify_status == VerifyStatus.explorer_unavailable
    assert w.status == WithdrawalStatus.verifying and w.verify_attempts == 1 and w.last_verify_at is not None


async def test_client_does_not_see_unverified_hash(api, container, clock, operator) -> None:
    h = await login_client(api, clock, 305)
    async with container.db() as s:
        client = (await s.execute(select(User).where(User.tg_id == 305))).scalar_one()
    await fund(container, client, "100")
    w = (
        await withdrawals.create(container, client.id, Network.TRC20, TRON_TO, Decimal(60), idempotency_key=None)
    ).withdrawal
    await withdrawals.take(container, w.id, operator)
    with respx.mock:
        mock_tron(respx.mock, "explorer_unavailable")
        await withdrawals.submit_hash(container, w.id, operator, TRON_TX)
    item = (await api.get("/api/client/history", headers=h)).json()["items"][0]
    assert item["status"] == "verifying" and item["tx_hash"] is None and item["explorer_url"] is None


async def test_b11_respects_antifraud_switch(container) -> None:
    await set_biz(container, b11_enabled=True)
    client = await make_user(container, tg_id=306, af_enabled=False)
    await fund(container, client, "100")
    assert (await withdrawals.get_config(container, client.id)).available == Decimal(100)


# --- antifraud --------------------------------------------------------------------------


async def test_new_incident_after_admin_cleared_flag_is_recorded(container, user) -> None:
    from tests.integration.test_antifraud import add_order, recalc

    for _ in range(3):
        await add_order(container, user, OrderStatus.expired)
    assert (await recalc(container, user)).is_suspicious
    async with container.db.begin() as s:
        (await s.get(User, user.id)).is_suspicious = False  # admin cleared the flag via PATCH
    await add_order(container, user, OrderStatus.cancelled)
    assert (await recalc(container, user)).is_suspicious
    async with container.db() as s:
        rules = [e.rule for e in (await s.execute(select(AntifraudEvent))).scalars()]
    assert rules.count("A2") == 2


async def test_each_fast_transit_is_a_new_event(container) -> None:
    await set_biz(container, b12_enabled=True)
    client = await make_user(container, tg_id=307)
    for _ in range(2):
        await fund(container, client, "100")
        await withdrawals.create(container, client.id, Network.TRC20, TRON_TO, Decimal(95), idempotency_key=None)
        container.clock.advance(minutes=1)
    async with container.db() as s:
        assert len((await s.execute(select(AntifraudEvent).where(AntifraudEvent.rule == "B12"))).scalars().all()) == 2


async def test_b8_auto_pause_is_not_throttled(container, user) -> None:
    from tests.integration.test_antifraud import add_order

    for i in range(20):
        await add_order(container, user, OrderStatus.paid if i < 7 else OrderStatus.expired, ago_min=30)
    await antifraud.global_conversion_watch(container)  # alert, no pause
    await set_biz(container, b8_auto_pause=True)
    await antifraud.global_conversion_watch(container)
    async with container.db() as s:
        assert (await settings_svc.load(s)).purchases_enabled is False


# --- rate and settings ------------------------------------------------------------------


@pytest.mark.parametrize("value", ['"NaN"', '"Infinity"', '"1e400"', "1e400"])
def test_rate_non_finite_values_rejected(value) -> None:
    body = '{"data":[{"symbol":"USDT/RUB","askPrice":' + value + '}],"code":0,"isWorking":1}'
    with pytest.raises(RateParseError):
        parse_rapira(body)


@pytest.mark.parametrize(
    "changes", [{"af_min_orders": 40, "af_window_orders": 30}, {"rate_ttl_seconds": 600, "rate_max_age_seconds": 180}]
)
async def test_settings_cross_validation(sessionmaker, clock, changes) -> None:
    async with sessionmaker.begin() as s:
        with pytest.raises(ServiceError):
            await settings_svc.update(s, changes, staff_id=None, now=clock.now())


# --- Telegram / jobs --------------------------------------------------------------------


async def test_scheduler_loop_survives_connection_errors(container, monkeypatch) -> None:
    calls: list[int] = []

    async def job(c):
        calls.append(1)

    sched = Scheduler(container, [Job("flaky", job, 0.01)])
    real_run_once = sched.run_once
    state = {"n": 0}

    async def run_once(j):
        state["n"] += 1
        if state["n"] <= 2:
            raise OSError("database restarting")  # e.g. engine.connect() fails outside the job body
        return await real_run_once(j)

    monkeypatch.setattr(sched, "run_once", run_once)
    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for(asyncio.wait_for))
    sched.start()
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.05)
    assert sched.running and calls
    await sched.stop()


def _fast_wait_for(real):
    async def wait_for(aw, timeout):
        return await real(aw, min(timeout, 0.01))

    return wait_for


async def test_staff_alert_retried_when_delivery_fails(container, fake_tg, clock) -> None:
    async with container.db.begin() as s:
        row = await notify.alert(s, "critical", now=clock.now())
    fake_tg.script("sendMessage", *[{"ok": False, "error_code": 502, "description": "Bad Gateway"}] * 4)
    await send_notifications(container)
    fresh = await reload(container, type(row), row.id)
    assert fresh.sent_at is None and not fresh.failed
    clock.advance(minutes=1)
    await send_notifications(container)
    fresh = await reload(container, type(row), row.id)
    assert fresh.sent_at is not None and fresh.payload["delivered"] == [-100500]


async def test_unparsable_html_falls_back_to_plain_text(container, fake_tg, clock) -> None:
    async with container.db.begin() as s:
        row = await notify.message(s, 55, "broken <tag", now=clock.now())
    fake_tg.script(
        "sendMessage",
        {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities: Unsupported start tag"},
    )
    await send_notifications(container)
    assert (await reload(container, type(row), row.id)).sent_at is not None
    assert "parse_mode" not in fake_tg.of("sendMessage")[-1].params


async def test_old_withdrawal_preview_cannot_confirm(bot, container, fake_tg) -> None:
    await bot.register(400)
    async with container.db() as s:
        client = (await s.execute(select(User).where(User.tg_id == 400))).scalar_one()
    await fund(container, client, "500")
    await bot.send(400, "/withdraw")
    await bot.click(400, "wd:net:TRC20")
    await bot.send(400, TRON_TO)
    await bot.send(400, "60")
    first_preview = fake_tg.next_message_id
    await bot.send(400, "/withdraw")
    await bot.click(400, "wd:net:BEP20")
    await bot.send(400, BSC_TO)
    await bot.send(400, "300")
    await bot.click(400, "wd:confirm", message_id=first_preview)
    async with container.db() as s:
        assert (await s.execute(select(Withdrawal))).scalars().all() == []
    await bot.click(400, "wd:confirm", message_id=fake_tg.next_message_id)
    async with container.db() as s:
        (w,) = (await s.execute(select(Withdrawal))).scalars().all()
    assert w.network == Network.BEP20 and w.amount == Decimal(300)


async def test_failed_callback_answer_does_not_abort_purchase(bot, container, fake_tg) -> None:
    await seed_rate(container, "100")
    await bot.register(401)
    await bot.send(401, "/buy")
    await bot.send(401, "1000")
    fake_tg.script(
        "answerCallbackQuery", {"ok": False, "error_code": 400, "description": "Bad Request: query is too old"}
    )
    await bot.click(401, "buy:confirm:1000:9.50", message_id=5000)
    assert fake_tg.of("sendPhoto")


async def test_error_shown_after_early_callback_answer(bot, container, fake_tg) -> None:
    await bot.register(402)
    async with container.db() as s:
        client = (await s.execute(select(User).where(User.tg_id == 402))).scalar_one()
    await fund(container, client, "100")
    await bot.send(402, "/withdraw")
    await bot.click(402, "wd:net:TRC20")
    await bot.send(402, TRON_TO)
    await set_biz(container, withdrawals_enabled=False)
    await bot.click(402, "wd:all")
    assert "Вывод временно приостановлен" in bot.last(402)


# --- auth / API ---------------------------------------------------------------------------


async def _web_staff(api, container, clock, tg_id=7100, login="web.victim"):
    headers = await login_staff(api, container, clock, tg_id)
    await api.post(
        "/api/admin/profile/web-credentials",
        headers=headers,
        json={"login": login, "password": "correct horse battery"},
    )
    async with container.db.begin() as s:
        staff = (await s.execute(select(Staff).where(Staff.tg_id == tg_id))).scalar_one()
        staff.totp_secret, staff.totp_enabled = "JBSWY3DPEHPK3PXP", True
    return headers


async def test_parallel_bruteforce_is_capped(api, container, clock) -> None:
    await _web_staff(api, container, clock)
    results = await asyncio.gather(
        *[
            api.post("/api/auth/password", json={"login": "web.victim", "password": f"bad-{i}", "totp": "1"})
            for i in range(15)
        ]
    )
    assert sorted({r.status_code for r in results}) == [401, 429]
    async with container.db() as s:
        assert len((await s.execute(select(LoginAttempt))).scalars().all()) == 5


def test_rate_limiter_sweeps_stale_keys() -> None:
    from app.api.deps import RateLimiter

    lim = RateLimiter()
    for i in range(5000):
        lim.hit(f"k{i}", 10, 60, now=0)
    for i in range(1000):
        lim.hit("fresh", 10_000, 60, now=4000 + i * 0.001)
    assert len(lim) < 10


async def test_removed_superadmin_loses_protection(container, clock) -> None:
    async with container.db.begin() as s:
        await staff_svc.sync_superadmins(s, [5555], clock.now())
    async with container.db.begin() as s:
        await staff_svc.sync_superadmins(s, [6666], clock.now())
    async with container.db() as s:
        old = (await s.execute(select(Staff).where(Staff.tg_id == 5555))).scalar_one()
    assert old.is_superadmin is False


async def test_invite_username_validated(container, admin) -> None:
    with pytest.raises(ServiceError) as exc:
        await staff_svc.add(container, admin, role="operator", username="<b>evil</b>")
    assert exc.value.code == "invalid_username"


async def test_request_body_size_limit(api) -> None:
    resp = await api.post(
        "/api/auth/password", content=b"{" + b" " * 70_000 + b"}", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "too_large"


async def test_credential_change_revokes_browser_sessions(api, container, clock) -> None:
    tg = await _web_staff(api, container, clock, tg_id=7101, login="web.owner")
    async with container.db.begin() as s:
        staff = (await s.execute(select(Staff).where(Staff.tg_id == 7101))).scalar_one()
        s.add(
            AuthSession(
                token_hash="x" * 64,
                subject_type="staff",
                subject_id=staff.id,
                auth_method="password",
                last_used_at=clock.now(),
                expires_at=clock.now().replace(year=2030),
            )
        )
    await api.post("/api/admin/profile/totp/setup", headers=tg)
    async with container.db() as s:
        sess = (await s.execute(select(AuthSession).where(AuthSession.token_hash == "x" * 64))).scalar_one()
    assert sess.revoked_at is not None
    assert (await api.get("/api/admin/me", headers=tg)).status_code == 200  # the Telegram session stays


async def test_bad_numbers_and_ids_give_400(api, container, clock) -> None:
    admin = await login_staff(api, container, clock, 7102)
    client = await login_client(api, clock, 7103)
    async with container.db() as s:
        uid = (await s.execute(select(User.id).where(User.tg_id == 7103))).scalar_one()
    for body in ({"markup_pct": "NaN"}, {"markup_pct": "1.234"}, {"order_max_rub": 10**12}):
        assert (await api.patch(f"/api/admin/users/{uid}", headers=admin, json=body)).status_code == 400
    for amount in ("NaN", "1e30", "0.0000001"):
        resp = await api.post(
            f"/api/admin/users/{uid}/adjust-balance",
            headers=admin,
            json={"amount": amount, "comment": "long enough comment"},
        )
        assert resp.status_code == 400, amount
    assert (await api.post("/api/admin/rate/manual", headers=admin, json={"rate": "NaN"})).status_code == 400
    assert (await api.get("/api/client/orders/10000000000000000000", headers=client)).status_code == 400
    assert (await api.get("/api/admin/users?cursor=99999999999999999999", headers=admin)).status_code == 400


def test_env_defaults_to_prod(monkeypatch) -> None:
    from app.config import ConfigError, load_config

    monkeypatch.delenv("ENV")
    with pytest.raises(ConfigError) as exc:  # mock provider is refused because the default is prod
        load_config(_env_file=None)
    assert "ALLOW_MOCK_IN_PROD" in str(exc.value)


async def test_500_has_security_headers(config, container, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.api import health
    from app.main import create_app

    async def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(container, "readiness", boom)
    app = create_app(config, container=container)
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        resp = await c.get("/readyz", headers={"X-Request-Id": "abc"})
    assert resp.status_code == 500
    assert resp.headers["x-content-type-options"] == "nosniff" and resp.headers["x-request-id"] == "abc"
    assert health is not None and uuid is not None
