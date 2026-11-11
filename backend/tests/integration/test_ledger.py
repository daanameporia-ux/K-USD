from __future__ import annotations

import random
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.domain.enums import KycStatus, LedgerType, UserStatus
from app.domain.errors import ServiceError
from app.models import LedgerEntry, Order, Staff, User
from app.services import ledger
from app.services import users as users_svc


async def make_user(s, tg_id=1, **kw) -> User:
    user = User(tg_id=tg_id, markup_pct=Decimal(5), status=UserStatus.active, kyc_status=KycStatus.passed, **kw)
    s.add(user)
    await s.flush()
    return user


async def make_order(s, user, n=1) -> Order:
    from datetime import UTC, datetime

    order = Order(
        public_id=f"B-{n}",
        user_id=user.id,
        amount_rub=1000,
        rate=Decimal(100),
        rate_source="rapira",
        markup_pct=Decimal(5),
        usdt_amount=Decimal("9.5"),
        margin_rub=Decimal(50),
        status="paid",
        provider="mock",
        source="bot",
        expires_at=datetime.now(UTC),
    )
    s.add(order)
    await s.flush()
    return order


async def test_operations_and_summary(sessionmaker, clock) -> None:
    now = clock.now()
    async with sessionmaker.begin() as s:
        user = await make_user(s)
        order = await make_order(s, user)
        staff = Staff(role="admin")
        s.add(staff)
        await s.flush()
        user = await users_svc.lock(s, user.id)
        await ledger.deposit(s, user, Decimal("100"), order_id=order.id, now=now)
        await ledger.hold(s, user, Decimal("60"), withdrawal_id=None, now=now)  # type: ignore[arg-type]
        assert (user.balance, user.hold) == (Decimal(40), Decimal(60))
        await ledger.release(s, user, Decimal("10"), withdrawal_id=None, now=now)  # type: ignore[arg-type]
        await ledger.complete(s, user, Decimal("50"), withdrawal_id=None, now=now)  # type: ignore[arg-type]
        assert (user.balance, user.hold) == (Decimal(50), Decimal(0))
        entry = await ledger.adjust(s, user, Decimal("-5.5"), staff_id=staff.id, comment="correction of test", now=now)
        assert entry.balance_after == Decimal("44.5")
        info = await ledger.summary(s, user)
    assert info.available == Decimal("44.5")
    assert info.total_bought == Decimal(100)
    assert info.total_withdrawn == Decimal(50)
    async with sessionmaker() as s:
        assert await ledger.check_invariant(s) == []


async def test_insufficient_funds_and_adjust_rules(sessionmaker, clock) -> None:
    now = clock.now()
    async with sessionmaker.begin() as s:
        user = await make_user(s)
        staff = Staff(role="admin")
        s.add(staff)
        await s.flush()
        with pytest.raises(ledger.InsufficientFunds):
            await ledger.hold(s, user, Decimal("1"), withdrawal_id=None, now=now)  # type: ignore[arg-type]
        with pytest.raises(ServiceError) as exc:
            await ledger.adjust(s, user, Decimal("-1"), staff_id=staff.id, comment="long enough comment", now=now)
        assert exc.value.code == "negative_balance"
        with pytest.raises(ServiceError) as exc:
            await ledger.adjust(s, user, Decimal("1"), staff_id=staff.id, comment="short", now=now)
        assert exc.value.code == "comment_required"
        with pytest.raises(ServiceError):
            await ledger.adjust(s, user, Decimal("0"), staff_id=staff.id, comment="long enough comment", now=now)
        with pytest.raises(ServiceError):
            await ledger.post(s, user, LedgerType.adjustment, balance_delta=Decimal(1), hold_delta=Decimal(0), now=now)
        with pytest.raises(ValueError):
            await ledger.deposit(s, user, Decimal(0), order_id=1, now=now)
        with pytest.raises(ValueError):
            await ledger.hold(s, user, Decimal(0), withdrawal_id=1, now=now)
        assert user.balance == 0


async def test_second_deposit_for_same_order_rejected_by_db(sessionmaker, clock) -> None:
    async with sessionmaker.begin() as s:
        user = await make_user(s)
        order = await make_order(s, user)
        await ledger.deposit(s, user, Decimal("9.5"), order_id=order.id, now=clock.now())
    with pytest.raises(IntegrityError):
        async with sessionmaker.begin() as s:
            user = await users_svc.lock(s, 1)
            await ledger.deposit(s, user, Decimal("9.5"), order_id=order.id, now=clock.now())


async def test_random_sequence_keeps_invariant(sessionmaker, clock) -> None:
    rnd = random.Random(42)
    async with sessionmaker.begin() as s:
        users = [await make_user(s, tg_id=i) for i in range(1, 4)]
        staff = Staff(role="admin")
        s.add(staff)
        await s.flush()
        n = 0
        for _ in range(300):
            user = rnd.choice(users)
            op = rnd.choice(["deposit", "hold", "release", "complete", "adjust"])
            amount = Decimal(rnd.randint(1, 10_000)) / 100
            try:
                if op == "deposit":
                    n += 1
                    order = await make_order(s, user, n)
                    await ledger.deposit(s, user, amount, order_id=order.id, now=clock.now())
                elif op == "hold":
                    await ledger.hold(s, user, amount, withdrawal_id=None, now=clock.now())  # type: ignore[arg-type]
                elif op == "release":
                    await ledger.release(s, user, amount, withdrawal_id=None, now=clock.now())  # type: ignore[arg-type]
                elif op == "complete":
                    await ledger.complete(s, user, amount, withdrawal_id=None, now=clock.now())  # type: ignore[arg-type]
                else:
                    sign = rnd.choice([-1, 1])
                    await ledger.adjust(
                        s, user, sign * amount, staff_id=staff.id, comment="random adjustment", now=clock.now()
                    )
            except ServiceError:
                pass
            assert user.balance >= 0 and user.hold >= 0
    async with sessionmaker() as s:
        assert await ledger.check_invariant(s) == []
        count = len((await s.execute(select(LedgerEntry))).scalars().all())
    assert count > 100


async def test_invariant_detects_corruption(sessionmaker, clock) -> None:
    async with sessionmaker.begin() as s:
        user = await make_user(s)
        order = await make_order(s, user)
        await ledger.deposit(s, user, Decimal("10"), order_id=order.id, now=clock.now())
        await make_user(s, tg_id=2)
    async with sessionmaker.begin() as s:
        await s.execute(text("UPDATE users SET balance = 11 WHERE tg_id = 1"))
        await s.execute(text("UPDATE users SET hold = 1 WHERE tg_id = 2"))
    async with sessionmaker() as s:
        mismatches = await ledger.check_invariant(s)
    assert sorted((m.user_id, m.balance, m.ledger_balance, m.hold, m.ledger_hold) for m in mismatches) == [
        (1, Decimal(11), Decimal(10), Decimal(0), Decimal(0)),
        (2, Decimal(0), Decimal(0), Decimal(1), Decimal(0)),
    ]
