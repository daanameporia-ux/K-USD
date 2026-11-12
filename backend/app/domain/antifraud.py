"""Antifraud rules as pure functions: data in, triggers / decisions out."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.enums import FINISHED_ORDER_STATUSES, AfSeverity, OrderStatus

HUNDRED = Decimal(100)


@dataclass(frozen=True)
class FinishedOrder:
    status: OrderStatus
    finished_at: datetime


@dataclass(frozen=True)
class Trigger:
    rule: str
    severity: AfSeverity
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_orders(self) -> bool:
        return self.severity == AfSeverity.block


def only_finished(orders: Sequence[FinishedOrder]) -> list[FinishedOrder]:
    """Keep finished orders (paid/expired/cancelled/rejected), newest first."""
    finished = [o for o in orders if o.status in FINISHED_ORDER_STATUSES]
    return sorted(finished, key=lambda o: o.finished_at, reverse=True)


def conversion_pct(paid: int, finished: int) -> Decimal | None:
    if finished == 0:
        return None
    return (Decimal(paid) * HUNDRED / Decimal(finished)).quantize(Decimal("0.01"))


def unpaid_streak(finished_desc: Sequence[FinishedOrder]) -> int:
    streak = 0
    for order in finished_desc:
        if order.status == OrderStatus.paid:
            break
        streak += 1
    return streak


@dataclass(frozen=True)
class MainParams:
    window: int = 30
    min_orders: int = 10
    min_conversion_pct: Decimal = Decimal(80)
    streak_suspicious: int = 3
    streak_block: int = 5


def rule_a1(finished_desc: Sequence[FinishedOrder], p: MainParams) -> Trigger | None:
    window = list(finished_desc[: p.window])
    if len(window) < p.min_orders:
        return None
    paid = sum(1 for o in window if o.status == OrderStatus.paid)
    pct = conversion_pct(paid, len(window))
    assert pct is not None
    if pct < p.min_conversion_pct:
        return Trigger(
            "A1",
            AfSeverity.block,
            {"paid": paid, "finished": len(window), "conversion_pct": str(pct), "threshold": str(p.min_conversion_pct)},
        )
    return None


def rule_a2(finished_desc: Sequence[FinishedOrder], p: MainParams) -> Trigger | None:
    streak = unpaid_streak(finished_desc)
    if streak >= p.streak_suspicious:
        return Trigger("A2", AfSeverity.suspicious, {"streak": streak, "threshold": p.streak_suspicious})
    return None


def rule_a3(finished_desc: Sequence[FinishedOrder], p: MainParams) -> Trigger | None:
    streak = unpaid_streak(finished_desc)
    if streak >= p.streak_block:
        return Trigger("A3", AfSeverity.block, {"streak": streak, "threshold": p.streak_block})
    return None


def evaluate_main(orders: Sequence[FinishedOrder], p: MainParams) -> list[Trigger]:
    finished = only_finished(orders)
    return [t for t in (rule_a1(finished, p), rule_a2(finished, p), rule_a3(finished, p)) if t is not None]


# --- B2: order frequency -----------------------------------------------------------------


def rate_limit_until(created: Sequence[datetime], now: datetime, per_hour: int, per_day: int) -> datetime | None:
    """When the next order may be created, or None if allowed now."""
    hour = sorted(t for t in created if t > now - timedelta(hours=1))
    day = sorted(t for t in created if t > now - timedelta(days=1))
    candidates: list[datetime] = []
    if len(hour) >= per_hour:
        candidates.append(hour[len(hour) - per_hour] + timedelta(hours=1))
    if len(day) >= per_day:
        candidates.append(day[len(day) - per_day] + timedelta(days=1))
    return max(candidates) if candidates else None


# --- B3: growing cooldown ------------------------------------------------------------------


def cooldown_until(
    finished_desc: Sequence[FinishedOrder], steps: Sequence[tuple[int, int]], now: datetime
) -> datetime | None:
    streak = unpaid_streak(finished_desc)
    minutes = 0
    for need, pause in sorted(steps):
        if streak >= need:
            minutes = pause
    if not minutes or not finished_desc:
        return None
    until = finished_desc[0].finished_at + timedelta(minutes=minutes)
    return until if until > now else None


# --- B9 / B10: amount limits ----------------------------------------------------------------


def newcomer_violation(
    paid_count: int, threshold: int, max_order: int, max_daily: int, amount: int, today_sum: int
) -> str | None:
    if paid_count >= threshold:
        return None
    if amount > max_order:
        return f"Для новых клиентов максимальная сумма заявки — {max_order} ₽."
    if today_sum + amount > max_daily:
        return f"Для новых клиентов лимит — {max_daily} ₽ в сутки. Осталось: {max(max_daily - today_sum, 0)} ₽."
    return None


def daily_turnover_violation(limit: int, amount: int, today_sum: int) -> str | None:
    if today_sum + amount > limit:
        return f"Превышен суточный лимит {limit} ₽. Осталось на сегодня: {max(limit - today_sum, 0)} ₽."
    return None


# --- B12 / B13 / B14 / B8 -------------------------------------------------------------------


def fast_transit(
    deposits: Sequence[tuple[datetime, Decimal]],
    withdrawal_amount: Decimal,
    balance_before: Decimal,
    now: datetime,
    window_minutes: int,
    balance_pct: Decimal,
) -> Trigger | None:
    recent = [d for d in deposits if d[0] > now - timedelta(minutes=window_minutes)]
    if not recent or balance_before <= 0:
        return None
    share = withdrawal_amount * HUNDRED / balance_before
    if share >= balance_pct:
        return Trigger(
            "B12",
            AfSeverity.suspicious,
            {"withdrawal": str(withdrawal_amount), "balance_before": str(balance_before), "share_pct": f"{share:.2f}"},
        )
    return None


def many_payers(payers: Sequence[str], max_payers: int) -> Trigger | None:
    distinct = sorted(set(payers))
    if len(distinct) > max_payers:
        return Trigger("B13", AfSeverity.suspicious, {"payers": len(distinct), "threshold": max_payers})
    return None


def daily_withdraw_violation(limit: Decimal, amount: Decimal, today_sum: Decimal) -> str | None:
    if today_sum + amount > limit:
        return f"Превышен суточный лимит вывода {limit} USDT. Осталось на сегодня: {max(limit - today_sum, 0)} USDT."
    return None


def global_conversion_drop(paid: int, finished: int, min_orders: int, min_pct: Decimal) -> Trigger | None:
    if finished < min_orders:
        return None
    pct = conversion_pct(paid, finished)
    assert pct is not None
    if pct < min_pct:
        return Trigger("B8", AfSeverity.info, {"paid": paid, "finished": finished, "conversion_pct": str(pct)})
    return None
