from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain import antifraud as r
from app.domain.enums import AfSeverity, OrderStatus

S = OrderStatus
T0 = datetime(2026, 11, 11, 12, tzinfo=UTC)
P = r.MainParams()


def seq(*statuses: OrderStatus) -> list[r.FinishedOrder]:
    """Oldest first, one minute apart."""
    return [r.FinishedOrder(s, T0 + timedelta(minutes=i)) for i, s in enumerate(statuses)]


def finished(*statuses):
    return r.only_finished(seq(*statuses))


def test_a1_needs_minimum_orders() -> None:
    assert r.rule_a1(finished(*[S.expired] * 9), P) is None


def test_a1_triggers_below_threshold() -> None:
    t = r.rule_a1(finished(*[S.paid] * 7, *[S.expired] * 3), P)
    assert t is not None and t.rule == "A1" and t.severity == AfSeverity.block
    assert t.details["conversion_pct"] == "70.00"


def test_a1_at_threshold_ok() -> None:
    assert r.rule_a1(finished(*[S.paid] * 8, *[S.expired] * 2), P) is None


def test_a1_window_only_last_30() -> None:
    old_bad = [S.expired] * 20
    recent_good = [S.paid] * 30
    assert r.rule_a1(finished(*old_bad, *recent_good), P) is None
    recent_bad = [S.paid] * 23 + [S.cancelled] * 7
    assert r.rule_a1(finished(*recent_bad), P) is not None


@pytest.mark.parametrize(("n", "a2", "a3"), [(2, False, False), (3, True, False), (4, True, False), (5, True, True)])
def test_streaks(n, a2, a3) -> None:
    orders = finished(S.paid, *[S.expired] * n)
    assert (r.rule_a2(orders, P) is not None) == a2
    assert (r.rule_a3(orders, P) is not None) == a3


def test_payment_resets_streak() -> None:
    orders = finished(*[S.expired] * 4, S.paid, S.cancelled)
    assert r.unpaid_streak(orders) == 1
    assert r.rule_a2(orders, P) is None


def test_failed_and_review_do_not_change_streak() -> None:
    orders = finished(S.expired, S.failed, S.expired, S.review, S.rejected, S.failed)
    assert r.unpaid_streak(orders) == 3
    assert r.rule_a2(orders, P) is not None
    assert [t.rule for t in r.evaluate_main(seq(S.expired, S.failed, S.expired, S.review, S.cancelled), P)] == ["A2"]


def test_rate_limit() -> None:
    now = T0
    created = [now - timedelta(minutes=m) for m in (5, 10, 20, 30)]
    assert r.rate_limit_until(created, now, 5, 15) is None
    created.append(now - timedelta(minutes=50))
    assert r.rate_limit_until(created, now, 5, 15) == now - timedelta(minutes=50) + timedelta(hours=1)
    day = [now - timedelta(hours=h) for h in range(2, 17)]
    assert r.rate_limit_until(day, now, 5, 15) == now - timedelta(hours=16) + timedelta(days=1)


def test_cooldown() -> None:
    steps = [(2, 5), (3, 15), (4, 60)]
    one = finished(S.paid, S.expired)
    assert r.cooldown_until(one, steps, one[0].finished_at) is None
    two = finished(S.expired, S.expired)
    last = two[0].finished_at
    assert r.cooldown_until(two, steps, last + timedelta(minutes=4)) == last + timedelta(minutes=5)
    assert r.cooldown_until(two, steps, last + timedelta(minutes=5)) is None
    four = finished(*[S.expired] * 6)
    last = four[0].finished_at
    assert r.cooldown_until(four, steps, last) == last + timedelta(minutes=60)
    assert r.cooldown_until([], steps, T0) is None


def test_amount_limits() -> None:
    assert r.newcomer_violation(3, 3, 20000, 50000, 90000, 0) is None
    assert "20000" in r.newcomer_violation(0, 3, 20000, 50000, 20001, 0)
    assert "50000" in r.newcomer_violation(2, 3, 20000, 50000, 20000, 40000)
    assert r.newcomer_violation(2, 3, 20000, 50000, 10000, 40000) is None
    assert r.daily_turnover_violation(100000, 50000, 50000) is None
    assert r.daily_turnover_violation(100000, 50001, 50000) is not None


def test_fast_transit() -> None:
    deposits = [(T0 - timedelta(minutes=5), Decimal(100))]
    assert r.fast_transit(deposits, Decimal(90), Decimal(100), T0, 10, Decimal(90)) is not None
    assert r.fast_transit(deposits, Decimal(89), Decimal(100), T0, 10, Decimal(90)) is None
    old = [(T0 - timedelta(minutes=11), Decimal(100))]
    assert r.fast_transit(old, Decimal(100), Decimal(100), T0, 10, Decimal(90)) is None
    assert r.fast_transit(deposits, Decimal(1), Decimal(0), T0, 10, Decimal(90)) is None


def test_many_payers_and_daily_withdraw_and_global() -> None:
    assert r.many_payers(["a", "b", "c", "a"], 3) is None
    assert r.many_payers(["a", "b", "c", "d"], 3) is not None
    assert r.daily_withdraw_violation(Decimal(100), Decimal(50), Decimal(50)) is None
    assert r.daily_withdraw_violation(Decimal(100), Decimal(51), Decimal(50)) is not None
    assert r.global_conversion_drop(1, 19, 20, Decimal(40)) is None
    assert r.global_conversion_drop(7, 20, 20, Decimal(40)) is not None
    assert r.global_conversion_drop(8, 20, 20, Decimal(40)) is None
    assert r.conversion_pct(0, 0) is None
