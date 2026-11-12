"""Antifraud orchestration: loads data, runs pure rules from domain.antifraud, applies effects."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import antifraud as rules
from app.domain.enums import FINISHED_ORDER_STATUSES, AfSeverity, LedgerType, OrderStatus, WithdrawalStatus
from app.domain.errors import NotFound, ServiceError
from app.domain.text import escape_html
from app.domain.timefmt import MSK, fmt_time, msk_date
from app.models import AntifraudEvent, LedgerEntry, Order, Setting, User, Withdrawal
from app.services import audit, notify
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.services.actor import Actor
from app.services.settings import BizSettings

if TYPE_CHECKING:
    from app.container import Container

RULE_TITLES = {
    "A1": "Низкая конверсия",
    "A2": "Серия неоплат — подозрение",
    "A3": "Серия неоплат — блокировка",
    "B4": "Общий адрес вывода у разных клиентов",
    "B8": "Глобальная просадка конверсии",
    "B12": "Быстрый транзит",
    "B13": "Много разных плательщиков",
}

TURNOVER_STATUSES = (OrderStatus.creating, OrderStatus.pending, OrderStatus.paid, OrderStatus.review)


def enabled_for(user: User, biz: BizSettings) -> bool:
    return biz.af_enabled and user.af_enabled


def main_params(biz: BizSettings) -> rules.MainParams:
    return rules.MainParams(
        window=biz.af_window_orders,
        min_orders=biz.af_min_orders,
        min_conversion_pct=biz.af_min_conversion_pct,
        streak_suspicious=biz.af_streak_suspicious,
        streak_block=biz.af_streak_block,
    )


def _ceil_minute(moment: datetime) -> datetime:
    """Round up so that "after HH:MM" is never earlier than the real moment."""
    floor = moment.replace(second=0, microsecond=0)
    return floor if floor == moment else floor + timedelta(minutes=1)


def msk_day_start(now: datetime) -> datetime:
    return datetime.combine(msk_date(now), time(0), tzinfo=MSK)


async def finished_orders(session: AsyncSession, user: User, limit: int) -> list[rules.FinishedOrder]:
    stmt = (
        select(Order.status, Order.finished_at)
        .where(
            Order.user_id == user.id,
            Order.status.in_(FINISHED_ORDER_STATUSES),
            Order.finished_at.is_not(None),
        )
        .order_by(Order.finished_at.desc(), Order.id.desc())
        .limit(limit)
    )
    if user.af_reset_at is not None:
        stmt = stmt.where(Order.finished_at > user.af_reset_at)
    rows = (await session.execute(stmt)).all()
    return [rules.FinishedOrder(r[0], r[1]) for r in rows if r[1] is not None]


async def _today_turnover(session: AsyncSession, user_id: int, now: datetime) -> int:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Order.amount_rub), 0)).where(
                Order.user_id == user_id,
                Order.status.in_(TURNOVER_STATUSES),
                Order.created_at >= msk_day_start(now),
            )
        )
    ).scalar_one()
    return int(total)


async def check_order_creation(
    session: AsyncSession, user: User, biz: BizSettings, amount_rub: int | None, now: datetime
) -> None:
    """B2, B3 (always), B9, B10 (when the amount is known). Raises ServiceError with a client message."""
    if not enabled_for(user, biz):
        return
    if biz.b2_enabled:
        since = now - timedelta(days=1)
        created = (
            await session.execute(
                select(Order.created_at).where(
                    Order.user_id == user.id, Order.created_at > since, Order.status != OrderStatus.failed
                )
            )
        ).scalars()
        until = rules.rate_limit_until(list(created), now, biz.b2_per_hour, biz.b2_per_day)
        if until is not None:
            raise ServiceError(
                "rate_limited",
                f"Слишком много заявок. Следующую можно создать после {fmt_time(_ceil_minute(until))} (МСК).",
                status=429,
            )
    if biz.b3_enabled:
        steps = biz.cooldown_steps
        depth = max(s for s, _ in steps)
        finished = await finished_orders(session, user, depth)
        until = rules.cooldown_until(finished, steps, now)
        if until is not None:
            raise ServiceError(
                "cooldown",
                f"После нескольких неоплаченных заявок действует пауза до {fmt_time(_ceil_minute(until))} (МСК).",
                status=429,
            )
    if amount_rub is None:
        return
    today: int | None = None
    if biz.b9_enabled:
        paid = (
            await session.execute(
                select(func.count(Order.id)).where(Order.user_id == user.id, Order.status == OrderStatus.paid)
            )
        ).scalar_one()
        today = await _today_turnover(session, user.id, now)
        msg = rules.newcomer_violation(
            int(paid), biz.b9_paid_orders_threshold, biz.b9_max_order_rub, biz.b9_max_daily_rub, amount_rub, today
        )
        if msg:
            raise ServiceError("newcomer_limit", msg)
    if biz.b10_enabled:
        limit = user.daily_turnover_limit_rub if user.daily_turnover_limit_rub is not None else biz.b10_daily_rub
        if today is None:
            today = await _today_turnover(session, user.id, now)
        msg = rules.daily_turnover_violation(limit, amount_rub, today)
        if msg:
            raise ServiceError("daily_limit", msg)


async def _has_open_event(session: AsyncSession, user_id: int | None, rule: str, key: str | None = None) -> bool:
    stmt = select(AntifraudEvent.id).where(AntifraudEvent.rule == rule, AntifraudEvent.resolved_at.is_(None))
    if key is not None:
        stmt = stmt.where(AntifraudEvent.details["key"].astext == key)
    stmt = (
        stmt.where(AntifraudEvent.user_id == user_id)
        if user_id is not None
        else stmt.where(AntifraudEvent.user_id.is_(None))
    )
    return (await session.execute(stmt.limit(1))).first() is not None


def _who(user: User) -> str:
    return f"@{user.tg_username}" if user.tg_username else f"tg {user.tg_id}"


async def record(
    session: AsyncSession,
    user: User | None,
    trigger: rules.Trigger,
    now: datetime,
    extra: str = "",
    *,
    key: str | None = None,
    unchanged: bool = True,
) -> AntifraudEvent | None:
    """Store an event and alert staff, unless the situation has not changed since an unresolved event.

    "Unchanged" means: an unresolved event of the same rule with the same situation key exists (`key`, e.g. the
    shared address for B4 or the withdrawal for B12) and its effect is still in place (`unchanged`, e.g. the
    user is still flagged — if an admin cleared the flag, a repeated trigger is a new situation).
    """
    user_id = user.id if user is not None else None
    if unchanged and await _has_open_event(session, user_id, trigger.rule, key):
        return None
    details = {**trigger.details, "key": key} if key is not None else trigger.details
    event = AntifraudEvent(
        user_id=user_id, rule=trigger.rule, severity=trigger.severity, details=details, created_at=now
    )
    session.add(event)
    await session.flush()
    title = RULE_TITLES.get(trigger.rule, trigger.rule)
    who = f" клиент {escape_html(_who(user))} (id {user.id})" if user is not None else ""
    icon = "⛔" if trigger.severity == AfSeverity.block else "⚠️"
    await notify.alert(
        session, f"{icon} Антифрод {trigger.rule} «{title}»:{who}. {escape_html(extra)}".strip(), now=now
    )
    return event


async def _apply(
    session: AsyncSession, user: User, trigger: rules.Trigger, now: datetime, key: str | None = None
) -> None:
    if trigger.severity == AfSeverity.block:
        in_effect = not user.can_create_orders
    elif trigger.severity == AfSeverity.suspicious:
        in_effect = user.is_suspicious
    else:
        in_effect = True
    if trigger.severity == AfSeverity.block:
        if user.can_create_orders:
            user.can_create_orders = False
            user.orders_block_reason = f"{trigger.rule}: {RULE_TITLES.get(trigger.rule, '')}"
    elif trigger.severity == AfSeverity.suspicious:
        user.is_suspicious = True
    await record(
        session, user, trigger, now, json.dumps(trigger.details, ensure_ascii=False), key=key, unchanged=in_effect
    )


def _payer_key(info: dict[str, Any] | None) -> str | None:
    if not info:
        return None
    for key in ("id", "payer_id", "phone", "account", "name"):
        if info.get(key):
            return f"{key}:{info[key]}"
    return json.dumps(info, sort_keys=True, ensure_ascii=False)


async def on_order_finished(session: AsyncSession, user: User, biz: BizSettings, now: datetime) -> None:
    """Recalculate A1–A3 (and B13) after a final order transition. `user` must be locked."""
    if not enabled_for(user, biz):
        return
    depth = max(biz.af_window_orders, biz.af_streak_block, biz.af_streak_suspicious)
    finished = await finished_orders(session, user, depth)
    for trigger in rules.evaluate_main(finished, main_params(biz)):
        await _apply(session, user, trigger, now)
    if biz.b13_enabled:
        rows = (
            await session.execute(
                select(Order.payer_info).where(
                    Order.user_id == user.id,
                    Order.status == OrderStatus.paid,
                    Order.paid_at > now - timedelta(days=biz.b13_window_days),
                    Order.payer_info.is_not(None),
                )
            )
        ).scalars()
        payers = [k for k in (_payer_key(r) for r in rows) if k]
        b13 = rules.many_payers(payers, biz.b13_max_payers)
        if b13 is not None:
            await _apply(session, user, b13, now, key=f"payers:{b13.details['payers']}")


async def check_withdrawal(session: AsyncSession, user: User, biz: BizSettings, amount: Decimal, now: datetime) -> None:
    """B14 daily withdrawal limit."""
    if not enabled_for(user, biz) or not biz.b14_enabled:
        return
    today = (
        await session.execute(
            select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(
                Withdrawal.user_id == user.id,
                Withdrawal.status.not_in((WithdrawalStatus.cancelled, WithdrawalStatus.rejected)),
                Withdrawal.created_at >= msk_day_start(now),
            )
        )
    ).scalar_one()
    msg = rules.daily_withdraw_violation(biz.b14_daily_usdt, amount, Decimal(today))
    if msg:
        raise ServiceError("daily_withdraw_limit", msg)


async def on_withdrawal_created(
    session: AsyncSession, user: User, withdrawal: Withdrawal, biz: BizSettings, now: datetime
) -> list[int]:
    """B4 shared address, B12 fast transit. `user` is locked by the caller.

    Returns ids of other clients that share the address: they are flagged by `flag_shared_address`
    in a separate transaction, so that two user rows are never locked in one transaction (no deadlocks).
    """
    other_ids: list[int] = []
    if not enabled_for(user, biz):
        return other_ids
    if biz.b4_enabled:
        others = (
            await session.execute(
                select(Withdrawal.user_id)
                .where(
                    Withdrawal.network == withdrawal.network,
                    func.lower(Withdrawal.address) == withdrawal.address.lower(),
                    Withdrawal.user_id != user.id,
                )
                .distinct()
            )
        ).scalars()
        other_ids = sorted(set(others))
        if other_ids:
            trigger = rules.Trigger(
                "B4",
                AfSeverity.suspicious,
                {"address": withdrawal.address, "network": withdrawal.network.value, "users": [user.id, *other_ids]},
            )
            await _apply(session, user, trigger, now, key=shared_key(withdrawal))
    if biz.b12_enabled:
        deposits = (
            await session.execute(
                select(LedgerEntry.created_at, LedgerEntry.balance_delta).where(
                    LedgerEntry.user_id == user.id,
                    LedgerEntry.type == LedgerType.deposit,
                    LedgerEntry.created_at > now - timedelta(minutes=biz.b12_window_minutes),
                )
            )
        ).all()
        b12 = rules.fast_transit(
            [(r[0], r[1]) for r in deposits],
            withdrawal.amount,
            user.balance + withdrawal.amount,
            now,
            biz.b12_window_minutes,
            biz.b12_balance_pct,
        )
        if b12 is not None:
            await _apply(session, user, b12, now, key=f"withdrawal:{withdrawal.id}")
    return other_ids


def shared_key(withdrawal: Withdrawal) -> str:
    return f"{withdrawal.network.value}:{withdrawal.address.lower()}"


async def flag_shared_address(c: Container, user_ids: list[int], withdrawal: Withdrawal) -> None:
    now = c.clock.now()
    for user_id in user_ids:
        async with c.db.begin() as session:
            biz = await settings_svc.load(session)
            user = await users_svc.lock(session, user_id)
            if not enabled_for(user, biz) or not biz.b4_enabled:
                continue
            trigger = rules.Trigger(
                "B4", AfSeverity.suspicious, {"withdrawal_id": withdrawal.id, "address": withdrawal.address}
            )
            await _apply(session, user, trigger, now, key=shared_key(withdrawal))


# --- admin actions ---------------------------------------------------------------------


async def unblock(c: Container, user_id: int, actor: Actor, comment: str, ip: str | None = None) -> User:
    actor.require_admin()
    if len((comment or "").strip()) < 3:
        raise ServiceError("comment_required", "Укажите комментарий к разблокировке.")
    now = c.clock.now()
    async with c.db.begin() as session:
        user = await users_svc.lock(session, user_id)
        before = {
            "can_create_orders": user.can_create_orders,
            "is_suspicious": user.is_suspicious,
            "af_reset_at": user.af_reset_at.isoformat() if user.af_reset_at else None,
        }
        user.can_create_orders = True
        user.orders_block_reason = None
        user.is_suspicious = False
        user.af_reset_at = now
        await session.execute(
            update(AntifraudEvent)
            .where(AntifraudEvent.user_id == user_id, AntifraudEvent.resolved_at.is_(None))
            .values(resolved_at=now, resolved_by=actor.id)
        )
        await audit.write(
            session,
            action="user.unblock_orders",
            staff_id=actor.id,
            entity_type="user",
            entity_id=user_id,
            before=before,
            after={"can_create_orders": True, "is_suspicious": False, "af_reset_at": now.isoformat()},
            comment=comment,
            ip=ip,
        )
        return user


async def block_orders(c: Container, user_id: int, actor: Actor, comment: str, ip: str | None = None) -> User:
    actor.require_admin()
    if len((comment or "").strip()) < 3:
        raise ServiceError("comment_required", "Укажите причину блокировки.")
    async with c.db.begin() as session:
        user = await users_svc.lock(session, user_id)
        was = user.can_create_orders
        user.can_create_orders = False
        user.orders_block_reason = f"Вручную: {comment.strip()}"
        await audit.write(
            session,
            action="user.block_orders",
            staff_id=actor.id,
            entity_type="user",
            entity_id=user_id,
            before={"can_create_orders": was},
            after={"can_create_orders": False},
            comment=comment,
            ip=ip,
        )
        return user


async def resolve_event(c: Container, event_id: int, actor: Actor, ip: str | None = None) -> AntifraudEvent:
    actor.require_admin()
    now = c.clock.now()
    async with c.db.begin() as session:
        event = await session.get(AntifraudEvent, event_id, with_for_update=True)
        if event is None:
            raise NotFound("Событие не найдено")
        if event.resolved_at is None:
            event.resolved_at = now
            event.resolved_by = actor.id
            await audit.write(
                session,
                action="antifraud.resolve",
                staff_id=actor.id,
                entity_type="antifraud_event",
                entity_id=event_id,
                ip=ip,
            )
        return event


# --- B8 job ----------------------------------------------------------------------------


async def global_conversion_watch(c: Container) -> rules.Trigger | None:
    now = c.clock.now()
    async with c.db.begin() as session:
        biz = await settings_svc.load(session)
        if not (biz.af_enabled and biz.b8_enabled):
            return None
        since = now - timedelta(minutes=biz.b8_window_minutes)
        if biz.b8_auto_pause and biz.purchases_enabled:
            # After an admin switched purchases back on, judge only what finished since then: otherwise the same
            # bad orders would pause purchases again on the next run, until they leave the window.
            resumed_at = (
                await session.execute(
                    select(Setting.updated_at).where(
                        Setting.key == "purchases_enabled", Setting.updated_by.is_not(None)
                    )
                )
            ).scalar_one_or_none()
            if resumed_at is not None and resumed_at > since:
                since = resumed_at
        row = (
            await session.execute(
                select(
                    func.count(Order.id).filter(Order.status == OrderStatus.paid),
                    func.count(Order.id),
                ).where(Order.status.in_(FINISHED_ORDER_STATUSES), Order.finished_at > since)
            )
        ).one()
        trigger = rules.global_conversion_drop(int(row[0]), int(row[1]), biz.b8_min_orders, biz.b8_min_conversion_pct)
        if trigger is None:
            return None
        extra = f"Конверсия {trigger.details['conversion_pct']}% за {biz.b8_window_minutes} мин."
        paused = False
        if biz.b8_auto_pause and biz.purchases_enabled:
            await settings_svc.update(
                session, {"purchases_enabled": False}, staff_id=None, now=now, comment="B8 auto pause"
            )
            extra += " Покупки автоматически приостановлены."
            paused = True
        alert_allowed = c.throttle.allow("b8", now, timedelta(minutes=biz.b8_window_minutes))
        if not paused and not alert_allowed:
            return trigger  # only the alert is throttled; the auto-pause above is always applied
        session.add(
            AntifraudEvent(user_id=None, rule="B8", severity=AfSeverity.info, details=trigger.details, created_at=now)
        )
        await notify.alert(session, f"⚠️ Антифрод B8 «{RULE_TITLES['B8']}». {extra}", now=now)
        return trigger
