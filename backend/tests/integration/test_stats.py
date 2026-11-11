from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.enums import Network, OrderSource, OrderStatus, WithdrawalStatus
from app.models import Order, Withdrawal
from app.services import settings as settings_svc
from app.services import stats
from tests.conftest import make_user, seed_rate

S = OrderStatus


async def order(
    container, user, status, created_at, *, amount=1000, pay_after=timedelta(minutes=2), paid_amount=None
) -> None:
    async with container.db.begin() as s:
        o = Order(
            public_id=f"t{created_at.timestamp()}{status}",
            user_id=user.id,
            amount_rub=amount,
            rate=Decimal(100),
            rate_source="rapira",
            markup_pct=Decimal(5),
            usdt_amount=Decimal(amount) * Decimal("0.0095"),
            margin_rub=Decimal(amount) * Decimal("0.05"),
            status=status,
            provider="mock",
            source=OrderSource.bot,
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=15),
            paid_amount_rub=paid_amount,
        )
        if status == S.paid:
            o.paid_at = created_at + pay_after
            o.credited_usdt = o.usdt_amount
        if status in (S.paid, S.expired, S.cancelled, S.rejected):
            o.finished_at = created_at + pay_after
        s.add(o)


async def dataset(container):
    user = await make_user(container)
    other = await make_user(container, tg_id=200)
    # 23:59 MSK on Nov 10 and 00:01 MSK on Nov 11
    await order(container, user, S.paid, datetime(2026, 11, 10, 20, 59, tzinfo=UTC), pay_after=timedelta(seconds=30))
    await order(container, user, S.expired, datetime(2026, 11, 10, 21, 1, tzinfo=UTC))
    today = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
    await order(container, user, S.paid, today, amount=5000, pay_after=timedelta(minutes=4))
    await order(container, user, S.paid, today + timedelta(minutes=10), amount=2000, paid_amount=2000)
    await order(container, user, S.cancelled, today + timedelta(minutes=20))
    await order(container, user, S.review, today + timedelta(minutes=30))
    await order(container, user, S.failed, today + timedelta(minutes=40))
    await order(container, user, S.pending, today + timedelta(minutes=50))
    await order(container, other, S.paid, today + timedelta(minutes=5), amount=10000)
    async with container.db.begin() as s:
        s.add(
            Withdrawal(
                public_id="W-1",
                user_id=user.id,
                network=Network.TRC20,
                address="T",
                amount=Decimal(60),
                fee=Decimal(4),
                amount_to_send=Decimal(56),
                status=WithdrawalStatus.completed,
                created_at=today,
                finished_at=today + timedelta(minutes=1),
                verify_details={},
            )
        )
        s.add(
            Withdrawal(
                public_id="W-2",
                user_id=user.id,
                network=Network.BEP20,
                address="0x",
                amount=Decimal(10),
                fee=Decimal(2),
                amount_to_send=Decimal(8),
                status=WithdrawalStatus.pending,
                created_at=today,
                verify_details={},
            )
        )
    return user, other


async def test_client_stats(container) -> None:
    user, _ = await dataset(container)
    async with container.db() as s:
        biz = await settings_svc.load(s)
        st = await stats.client_stats(s, user, biz)
        daily = await stats.client_daily(s, user.id)
    c = st.counts
    assert (c.created, c.paid, c.expired, c.cancelled, c.rejected, c.review, c.failed, c.pending) == (
        8,
        3,
        1,
        1,
        0,
        1,
        1,
        1,
    )
    assert c.finished == 5 and st.conversion_pct == Decimal("60.00")
    assert st.window_finished == 5 and st.window_conversion_pct == Decimal("60.00")
    assert st.unpaid_streak == 1
    assert st.turnover_rub == 8000 and st.usdt_received == Decimal("76.0000")
    assert st.margin_rub == Decimal("400.00") and st.avg_check_rub == Decimal("2666.67")
    assert st.median_pay_seconds == 120
    assert (st.withdrawals_count, st.withdrawals_sum, st.withdrawals_fees) == (1, Decimal(60), Decimal(4))
    assert st.first_deal_at == datetime(2026, 11, 10, 20, 59, 30, tzinfo=UTC)
    assert [d.day for d in daily] == [date(2026, 11, 11), date(2026, 11, 10)]
    nov11, nov10 = daily
    assert (nov10.counts.created, nov10.counts.paid, nov10.conversion_pct) == (1, 1, Decimal("100.00"))
    assert (nov11.counts.created, nov11.counts.paid, nov11.counts.finished) == (7, 2, 4)
    assert nov11.conversion_pct == Decimal("50.00") and nov11.turnover_rub == 7000


async def test_dashboard(container, clock) -> None:
    await dataset(container)
    await seed_rate(container, "95")
    async with container.db() as s:
        biz = await settings_svc.load(s)
        dash = await stats.dashboard(s, biz, clock.now())
        daily = await stats.dashboard_daily(s, clock.now())
    today, yesterday = dash.periods["today"], dash.periods["yesterday"]
    assert today.orders.created == 8 and today.orders.paid == 3
    assert today.turnover_rub == 17000
    assert today.active_clients == 2 and today.new_clients == 2
    assert today.withdrawals_completed == 1 and today.fees_collected == Decimal(4)
    assert yesterday.orders.created == 1 and yesterday.turnover_rub == 1000
    assert dash.periods["7d"].orders.created == 9
    assert dash.queue_count == 1 and dash.queue_sum == Decimal(8)
    assert dash.rate is not None and dash.rate.value == Decimal(95)
    assert dash.af_blocked == 0 and dash.suspicious == 0
    assert [d.day for d in daily] == [date(2026, 11, 11), date(2026, 11, 10)]
