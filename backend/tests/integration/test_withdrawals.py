from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import respx
from sqlalchemy import select

from app.domain.enums import Network, StaffRole, VerifyStatus, WithdrawalStatus
from app.domain.errors import ServiceError
from app.models import AddressBlacklist, User, Withdrawal
from app.services import ledger, withdrawals
from app.services import settings as settings_svc
from app.services.actor import Actor
from tests.chain_fixtures import BSC_AMOUNT, BSC_TO, BSC_TX, TRON_AMOUNT, TRON_TO, TRON_TX, mock_bsc, mock_tron
from tests.conftest import fund, make_staff, make_user, outbox, reload

ADDR = {Network.TRC20: TRON_TO, Network.BEP20: BSC_TO}
TX = {Network.TRC20: TRON_TX, Network.BEP20: BSC_TX}
SEND = {Network.TRC20: TRON_AMOUNT, Network.BEP20: BSC_AMOUNT}
FEE = {Network.TRC20: Decimal(4), Network.BEP20: Decimal(2)}


@pytest.fixture
async def user(container):
    u = await make_user(container)
    await fund(container, u, "100")
    return u


@pytest.fixture
async def operator(container):
    s = await make_staff(container, tg_id=2001, role="operator")
    return Actor(s.id, StaffRole.operator, tg_id=2001)


@pytest.fixture
async def admin(container):
    s = await make_staff(container, tg_id=2002, role="admin")
    return Actor(s.id, StaffRole.admin, tg_id=2002)


async def create(container, user, amount="60", network=Network.TRC20, key=None, address=None):
    return await withdrawals.create(
        container, user.id, network, address or ADDR[network], Decimal(amount), idempotency_key=key
    )


async def balances(container, user):
    u = await reload(container, User, user.id)
    return u.balance, u.hold


async def test_create_holds_funds(container, user) -> None:
    result = await create(container, user, "60", key="k")
    w = result.withdrawal
    assert result.created and w.public_id.startswith("W-") and w.status == WithdrawalStatus.pending
    assert (w.amount, w.fee, w.amount_to_send) == (Decimal(60), Decimal(4), Decimal(56))
    assert await balances(container, user) == (Decimal(40), Decimal(60))
    again = await create(container, user, "60", key="k")
    assert not again.created and again.withdrawal.id == w.id
    assert any("Новый вывод" in m["text"] for m in await outbox(container, 0))
    # BEP-20 address is stored in EIP-55 form
    await fund(container, user, "100")
    w2 = (await create(container, user, "50.5", Network.BEP20, address=BSC_TO.lower())).withdrawal
    assert w2.address == BSC_TO and w2.fee == Decimal(2) and w2.amount_to_send == Decimal("48.5")


@pytest.mark.parametrize(
    ("amount", "code"),
    [("101", "insufficient_funds"), ("49.99", "below_minimum"), ("50.001", "invalid_amount")],
)
async def test_create_refusals(container, user, amount, code) -> None:
    with pytest.raises(ServiceError) as exc:
        await create(container, user, amount)
    assert exc.value.code == code
    assert await balances(container, user) == (Decimal(100), Decimal(0))


async def test_fee_must_be_less_than_amount(container, user, clock) -> None:
    async with container.db.begin() as s:
        await settings_svc.update(s, {"withdraw_min_usdt": "1"}, staff_id=None, now=clock.now())
    with pytest.raises(ServiceError) as exc:
        await create(container, user, "4")
    assert exc.value.code == "amount_too_small"


async def test_address_checks_and_blacklist(container, user) -> None:
    with pytest.raises(ServiceError) as exc:
        await create(container, user, address="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert exc.value.code == "invalid_address"
    async with container.db.begin() as s:
        s.add(AddressBlacklist(network=Network.TRC20, address=TRON_TO, comment="scam"))
    with pytest.raises(ServiceError) as exc:
        await create(container, user)
    assert exc.value.code == "address_blacklisted"


async def test_disabled_and_inactive(container, user, clock) -> None:
    async with container.db.begin() as s:
        await settings_svc.update(s, {"withdrawals_enabled": False}, staff_id=None, now=clock.now())
    with pytest.raises(ServiceError) as exc:
        await withdrawals.start(container, user.id)
    assert exc.value.code == "withdrawals_disabled"


async def test_start_requires_minimum(container) -> None:
    u = await make_user(container, tg_id=555)
    await fund(container, u, "10")
    with pytest.raises(ServiceError) as exc:
        await withdrawals.start(container, u.id)
    assert "Минимальная сумма вывода — 50.00 USDT" in exc.value.message


async def test_parallel_full_balance_withdrawals(container, user) -> None:
    results = await asyncio.gather(
        create(container, user, "100", key="a"), create(container, user, "100", key="b"), return_exceptions=True
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 1
    assert isinstance(next(r for r in results if isinstance(r, Exception)), ServiceError)
    assert await balances(container, user) == (Decimal(0), Decimal(100))


async def test_cancel_returns_funds(container, user, operator) -> None:
    w = (await create(container, user)).withdrawal
    other = await make_user(container, tg_id=777)
    with pytest.raises(ServiceError) as exc:
        await withdrawals.cancel(container, other.id, w.id)
    assert exc.value.status == 404
    await withdrawals.cancel(container, user.id, w.id)
    assert await balances(container, user) == (Decimal(100), Decimal(0))
    w2 = (await create(container, user)).withdrawal
    await withdrawals.take(container, w2.id, operator)
    with pytest.raises(ServiceError):
        await withdrawals.cancel(container, user.id, w2.id)


async def test_reject_returns_funds(container, user, operator) -> None:
    w = (await create(container, user)).withdrawal
    with pytest.raises(ServiceError):
        await withdrawals.reject(container, w.id, operator, "")
    w = await withdrawals.reject(container, w.id, operator, "Адрес под санкциями")
    assert w.status == WithdrawalStatus.rejected and w.reject_reason == "Адрес под санкциями"
    assert await balances(container, user) == (Decimal(100), Decimal(0))
    msgs = await outbox(container, user.tg_id)
    assert "Причина: Адрес под санкциями" in msgs[-1]["text"]


async def test_take_is_atomic(container, user, operator, admin) -> None:
    w = (await create(container, user)).withdrawal
    results = await asyncio.gather(
        withdrawals.take(container, w.id, operator), withdrawals.take(container, w.id, admin), return_exceptions=True
    )
    assert sum(1 for r in results if not isinstance(r, Exception)) == 1
    err = next(r for r in results if isinstance(r, Exception))
    assert isinstance(err, ServiceError) and err.code == "already_taken"


async def test_release_and_assignee_rules(container, user, operator) -> None:
    other = Actor((await make_staff(container, tg_id=2003, role="operator")).id, StaffRole.operator)
    w = (await create(container, user)).withdrawal
    await withdrawals.take(container, w.id, operator)
    with pytest.raises(ServiceError) as exc:
        await withdrawals.submit_hash(container, w.id, other, TRON_TX)
    assert exc.value.status == 403
    w = await withdrawals.release_to_queue(container, w.id, operator)
    assert w.status == WithdrawalStatus.pending and w.assigned_staff_id is None
    with pytest.raises(ServiceError):
        await withdrawals.submit_hash(container, w.id, operator, TRON_TX)


async def prepare(container, user, network, actor, clock):
    """A withdrawal whose amount_to_send equals the fixture transfer, taken in work."""
    async with container.db.begin() as s:
        await settings_svc.update(s, {"withdraw_min_usdt": "1"}, staff_id=None, now=clock.now())
    w = (await create(container, user, str(SEND[network] + FEE[network]), network)).withdrawal
    await withdrawals.take(container, w.id, actor)
    return w


SCENARIOS = [
    ("ok", VerifyStatus.ok, WithdrawalStatus.completed),
    ("not_found", VerifyStatus.not_found, WithdrawalStatus.verifying),
    ("tx_failed", VerifyStatus.tx_failed, WithdrawalStatus.in_progress),
    ("wrong_token", VerifyStatus.wrong_token, WithdrawalStatus.in_progress),
    ("wrong_recipient", VerifyStatus.wrong_recipient, WithdrawalStatus.in_progress),
    ("wrong_amount", VerifyStatus.wrong_amount, WithdrawalStatus.in_progress),
    ("explorer_unavailable", VerifyStatus.explorer_unavailable, WithdrawalStatus.verifying),
]


@pytest.mark.parametrize("network", list(Network))
@pytest.mark.parametrize(("scenario", "verify_status", "final"), SCENARIOS)
async def test_verification_codes(container, user, operator, clock, network, scenario, verify_status, final) -> None:
    w = await prepare(container, user, network, operator, clock)
    with respx.mock:
        (mock_tron if network == Network.TRC20 else mock_bsc)(respx.mock, scenario)
        result = await withdrawals.submit_hash(container, w.id, operator, TX[network].upper())
    w = result.withdrawal
    assert result.verify_status == verify_status
    assert w.verify_status == verify_status and w.status == final
    details = w.verify_details
    assert details["expected"]["to"] == ADDR[network] and details["explorer_url"].endswith(TX[network])
    if final == WithdrawalStatus.completed:
        assert await balances(container, user) == (Decimal(100) - w.amount, Decimal(0))
        msg = (await outbox(container, user.tg_id))[-1]["text"]
        assert "исполнен" in msg and TX[network] in msg
    elif final == WithdrawalStatus.in_progress:
        assert w.tx_hash is None and details["tx_hash"] == TX[network]
        assert details["actual"]["found"] is True
        assert await balances(container, user) == (Decimal(100) - w.amount, w.amount)
    else:
        assert w.tx_hash == TX[network]


async def test_bsc_pending_confirmations(container, user, operator, clock) -> None:
    w = await prepare(container, user, Network.BEP20, operator, clock)
    with respx.mock:
        mock_bsc(respx.mock, "pending_confirmations")
        result = await withdrawals.submit_hash(container, w.id, operator, BSC_TX)
    assert result.verify_status == VerifyStatus.pending_confirmations
    assert result.withdrawal.status == WithdrawalStatus.verifying
    with respx.mock:
        mock_bsc(respx.mock, "ok")
        clock.advance(seconds=29)
        assert await withdrawals.verify_due(container) == 0
        clock.advance(seconds=2)
        assert await withdrawals.verify_due(container) == 1
    assert (await reload(container, Withdrawal, w.id)).status == WithdrawalStatus.completed


@pytest.mark.parametrize("network", list(Network))
async def test_too_old(container, user, operator, clock, network) -> None:
    clock.set(datetime(2026, 11, 12, tzinfo=UTC))
    w = await prepare(container, user, network, operator, clock)
    with respx.mock:
        (mock_tron if network == Network.TRC20 else mock_bsc)(respx.mock, "ok")
        result = await withdrawals.submit_hash(container, w.id, operator, TX[network])
    assert result.verify_status == VerifyStatus.too_old
    assert result.withdrawal.status == WithdrawalStatus.in_progress


async def test_not_found_times_out_after_30_minutes(container, user, operator, clock) -> None:
    w = await prepare(container, user, Network.TRC20, operator, clock)
    with respx.mock:
        mock_tron(respx.mock, "not_found")
        await withdrawals.submit_hash(container, w.id, operator, TRON_TX)
        clock.advance(minutes=29)
        await withdrawals.verify_due(container)
        assert (await reload(container, Withdrawal, w.id)).status == WithdrawalStatus.verifying
        clock.advance(minutes=1)
        await withdrawals.verify_due(container)
    w = await reload(container, Withdrawal, w.id)
    assert w.status == WithdrawalStatus.in_progress and w.verify_status == VerifyStatus.not_found
    assert w.verify_attempts == 3


async def test_duplicate_hash(container, user, operator, clock) -> None:
    w1 = await prepare(container, user, Network.TRC20, operator, clock)
    with respx.mock:
        mock_tron(respx.mock, "ok")
        await withdrawals.submit_hash(container, w1.id, operator, TRON_TX)
    w2 = (await create(container, user, "20")).withdrawal
    await withdrawals.take(container, w2.id, operator)
    with pytest.raises(ServiceError) as exc:
        await withdrawals.submit_hash(container, w2.id, operator, "0x" + TRON_TX.upper())
    assert exc.value.code == "duplicate_hash" and w1.public_id in exc.value.message
    assert (await reload(container, Withdrawal, w2.id)).status == WithdrawalStatus.in_progress
    with pytest.raises(ServiceError) as exc:
        await withdrawals.submit_hash(container, w2.id, operator, "nothex")
    assert exc.value.code == "invalid_hash"


async def test_b6_large_withdrawal_admin_only(container, operator, admin, clock) -> None:
    rich = await make_user(container, tg_id=900)
    await fund(container, rich, "6000")
    w = (await create(container, rich, "5000")).withdrawal
    await withdrawals.take(container, w.id, operator)
    with pytest.raises(ServiceError) as exc:
        await withdrawals.submit_hash(container, w.id, operator, TRON_TX)
    assert exc.value.code == "admin_only_amount"
    with respx.mock:
        mock_tron(respx.mock, "wrong_amount")
        result = await withdrawals.submit_hash(container, w.id, admin, TRON_TX)
    assert result.verify_status == VerifyStatus.wrong_amount
    # B6 disabled -> the operator may close it
    async with container.db.begin() as s:
        await settings_svc.update(s, {"b6_enabled": False}, staff_id=None, now=clock.now())
    with respx.mock:
        mock_tron(respx.mock, "not_found")
        result = await withdrawals.submit_hash(container, w.id, operator, TRON_TX)
    assert result.verify_status == VerifyStatus.not_found


async def test_force_complete(container, user, operator, admin) -> None:
    w = (await create(container, user)).withdrawal
    await withdrawals.take(container, w.id, operator)
    with pytest.raises(ServiceError) as exc:
        await withdrawals.force_complete(container, w.id, operator, "manual")
    assert exc.value.status == 403
    with pytest.raises(ServiceError):
        await withdrawals.force_complete(container, w.id, admin, "")
    w = await withdrawals.force_complete(container, w.id, admin, "sent from cold wallet")
    assert w.status == WithdrawalStatus.completed and w.forced
    assert await balances(container, user) == (Decimal(40), Decimal(0))
    assert any("принудительно" in m["text"] for m in await outbox(container, 0))
    with pytest.raises(ServiceError):
        await withdrawals.force_complete(container, w.id, admin, "again")


async def test_recheck(container, user, operator, clock) -> None:
    w = await prepare(container, user, Network.TRC20, operator, clock)
    with pytest.raises(ServiceError):
        await withdrawals.recheck(container, w.id, operator)
    with respx.mock:
        mock_tron(respx.mock, "explorer_unavailable")
        await withdrawals.submit_hash(container, w.id, operator, TRON_TX)
    with respx.mock:
        mock_tron(respx.mock, "ok")
        assert await withdrawals.recheck(container, w.id, operator) == VerifyStatus.ok


async def test_b11_hold_after_purchase(container, user, clock) -> None:
    async with container.db.begin() as s:
        await settings_svc.update(s, {"b11_enabled": True, "b11_hold_hours": 2}, staff_id=None, now=clock.now())
    cfg = await withdrawals.get_config(container, user.id)
    assert cfg.available == 0
    with pytest.raises(ServiceError):
        await create(container, user)
    clock.advance(hours=2, seconds=1)
    assert (await withdrawals.get_config(container, user.id)).available == Decimal(100)
    assert (await create(container, user)).created


async def test_ledger_invariant_after_flows(container, user, operator, admin, clock) -> None:
    w = (await create(container, user, "50")).withdrawal
    await withdrawals.take(container, w.id, operator)
    await withdrawals.reject(container, w.id, operator, "bad address")
    w = (await create(container, user, "50")).withdrawal
    await withdrawals.cancel(container, user.id, w.id)
    w = (await create(container, user, "50")).withdrawal
    await withdrawals.take(container, w.id, admin)
    await withdrawals.force_complete(container, w.id, admin, "done by hand")
    async with container.db() as s:
        assert await ledger.check_invariant(s) == []
        rows = (await s.execute(select(Withdrawal))).scalars().all()
    assert len(rows) == 3
