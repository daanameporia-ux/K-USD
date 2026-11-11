"""Admin API. Roles are enforced here on the backend for every endpoint (see 11.1)."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api import serializers as ser
from app.api.deps import MAX_ID, StaffAuth, admin_auth, client_ip, container, staff_auth
from app.container import Container
from app.domain.enums import AuthMethod, Network, OrderStatus, StaffRole, UserStatus, WithdrawalStatus
from app.domain.errors import Forbidden, NotFound, ServiceError
from app.domain.text import escape_html
from app.domain.timefmt import MSK
from app.models import AddressBlacklist, AntifraudEvent, AuditLog, LedgerEntry, Order, Staff, User, Withdrawal
from app.services import antifraud, audit, auth, ledger, notify, orders, rates, stats, withdrawals
from app.services import settings as settings_svc
from app.services import staff as staff_svc
from app.services import users as users_svc
from app.services.settings_meta import settings_meta

Id = Annotated[int, Path(ge=1, le=MAX_ID)]

router = APIRouter(prefix="/api/admin")

PAGE_MAX = 100


def _page(rows: list[Any], limit: int) -> tuple[list[Any], str | None]:
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, (str(rows[-1].id) if has_more and rows else None)


def _cursor(cursor: str | None) -> int | None:
    if cursor is None or cursor == "":
        return None
    try:
        value = int(cursor)
    except ValueError:
        raise ServiceError("bad_cursor", "Некорректный курсор") from None
    if not 0 < value <= MAX_ID:
        raise ServiceError("bad_cursor", "Некорректный курсор")
    return value


def _dec(value: str, field: str, *, places: int = 6, max_abs: Decimal = Decimal(10) ** 12) -> Decimal:
    """Finite decimal with at most `places` decimals and |value| < max_abs (fits NUMERIC(20,6))."""
    try:
        d = Decimal(value.replace(",", ".").strip())
    except (InvalidOperation, AttributeError, ValueError):
        raise ServiceError("invalid_number", f"Некорректное число в поле {field}") from None
    if not d.is_finite() or abs(d) >= max_abs or d != d.quantize(Decimal(1).scaleb(-places)):
        raise ServiceError(
            "invalid_number", f"Некорректное число в поле {field}: не больше {places} знаков после точки"
        )
    return d


# --- me ------------------------------------------------------------------------------


@router.get("/me")
async def me(sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)) -> dict[str, Any]:
    async with c.db() as session:
        biz = await settings_svc.load(session)
    return {
        "staff": ser.staff(sa.staff),
        "auth_method": sa.method.value,
        "is_admin": sa.staff.role == StaffRole.admin,
        "payment_provider": c.payments.default_name,
        "is_mock": c.payments.default_name == "mock",
        "withdraw_admin_only_from_usdt": ser.money(biz.withdraw_admin_only_from_usdt),
        "b6_enabled": biz.b6_enabled,
    }


# --- withdrawals -----------------------------------------------------------------------


class HashIn(BaseModel):
    tx_hash: str = Field(max_length=200)


class ReasonIn(BaseModel):
    reason: str = Field(max_length=1000)


class ForceIn(BaseModel):
    comment: str = Field(max_length=1000)
    tx_hash: str | None = Field(None, max_length=200)


class CommentIn(BaseModel):
    comment: str = Field(max_length=1000)


async def _withdrawal_view(c: Container, w: Withdrawal) -> dict[str, Any]:
    async with c.db() as session:
        user = await session.get(User, w.user_id)
        assigned = await session.get(Staff, w.assigned_staff_id) if w.assigned_staff_id else None
    data = ser.withdrawal(w, admin=True)
    data["user"] = (
        {"id": user.id, "tg_id": user.tg_id, "tg_username": user.tg_username, "is_suspicious": user.is_suspicious}
        if user
        else None
    )
    data["assigned_staff"] = ser.staff(assigned) if assigned else None
    return data


@router.get("/withdrawals")
async def list_withdrawals(
    status: str | None = Query(None, description="comma separated"),
    network: Network | None = None,
    q: str | None = Query(None, max_length=200),
    assigned: Literal["me"] | None = None,
    user_id: int | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    stmt = select(Withdrawal).order_by(Withdrawal.id.desc()).limit(limit + 1)
    if status:
        try:
            statuses = [WithdrawalStatus(s.strip()) for s in status.split(",") if s.strip()]
        except ValueError:
            raise ServiceError("bad_status", "Неизвестный статус") from None
        stmt = stmt.where(Withdrawal.status.in_(statuses))
    if network:
        stmt = stmt.where(Withdrawal.network == network)
    if assigned == "me":
        stmt = stmt.where(Withdrawal.assigned_staff_id == sa.staff.id)
    if user_id:
        stmt = stmt.where(Withdrawal.user_id == user_id)
    if q:
        term = q.strip()
        stmt = stmt.where(
            or_(
                Withdrawal.public_id == term.upper(),
                func.lower(Withdrawal.address) == term.lower(),
                Withdrawal.tx_hash == term.lower(),
            )
        )
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(Withdrawal.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
        users = {
            u.id: u
            for u in (await session.execute(select(User).where(User.id.in_({w.user_id for w in rows})))).scalars()
        }
    rows, nxt = _page(rows, limit)
    items = []
    for w in rows:
        item = ser.withdrawal(w, admin=True)
        u = users.get(w.user_id)
        item["user"] = {"id": u.id, "tg_id": u.tg_id, "tg_username": u.tg_username} if u else None
        items.append(item)
    return {"items": items, "next_cursor": nxt}


@router.get("/withdrawals/{withdrawal_id}")
async def get_withdrawal(
    withdrawal_id: Id, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        w = await session.get(Withdrawal, withdrawal_id)
    if w is None:
        raise NotFound("Вывод не найден")
    return await _withdrawal_view(c, w)


@router.post("/withdrawals/{withdrawal_id}/take")
async def take_withdrawal(
    withdrawal_id: Id, request: Request, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    return await _withdrawal_view(c, await withdrawals.take(c, withdrawal_id, sa.actor, client_ip(request)))


@router.post("/withdrawals/{withdrawal_id}/release")
async def release_withdrawal(
    withdrawal_id: Id, request: Request, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    return await _withdrawal_view(c, await withdrawals.release_to_queue(c, withdrawal_id, sa.actor, client_ip(request)))


@router.post("/withdrawals/{withdrawal_id}/submit-hash")
async def submit_hash(
    withdrawal_id: Id,
    body: HashIn,
    request: Request,
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    result = await withdrawals.submit_hash(c, withdrawal_id, sa.actor, body.tx_hash, client_ip(request))
    return await _withdrawal_view(c, result.withdrawal)


@router.post("/withdrawals/{withdrawal_id}/reject")
async def reject_withdrawal(
    withdrawal_id: Id,
    body: ReasonIn,
    request: Request,
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    return await _withdrawal_view(
        c, await withdrawals.reject(c, withdrawal_id, sa.actor, body.reason, client_ip(request))
    )


@router.post("/withdrawals/{withdrawal_id}/force-complete")
async def force_complete(
    withdrawal_id: Id,
    body: ForceIn,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    w = await withdrawals.force_complete(
        c, withdrawal_id, sa.actor, body.comment, client_ip(request), tx_hash=body.tx_hash or None
    )
    return await _withdrawal_view(c, w)


@router.post("/withdrawals/{withdrawal_id}/recheck")
async def recheck(
    withdrawal_id: Id, request: Request, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    await withdrawals.recheck(c, withdrawal_id, sa.actor, client_ip(request))
    async with c.db() as session:
        w = await session.get(Withdrawal, withdrawal_id)
    assert w is not None
    return await _withdrawal_view(c, w)


# --- orders ------------------------------------------------------------------------------


class ResolveReviewIn(BaseModel):
    decision: Literal["credit_order_rate", "credit_current_rate", "reject"]
    comment: str | None = Field(None, max_length=1000)


class SimulateIn(BaseModel):
    amount_rub: int | None = Field(None, ge=1, le=100_000_000)


def _msk_start(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=MSK)


@router.get("/orders")
async def list_orders(
    status: OrderStatus | None = None,
    user_id: int | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    q: str | None = Query(None, max_length=100),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    from datetime import timedelta

    stmt = select(Order).order_by(Order.id.desc()).limit(limit + 1)
    if status:
        stmt = stmt.where(Order.status == status)
    if user_id:
        stmt = stmt.where(Order.user_id == user_id)
    if from_:
        stmt = stmt.where(Order.created_at >= _msk_start(from_))
    if to:
        stmt = stmt.where(Order.created_at < _msk_start(to) + timedelta(days=1))
    if q:
        stmt = stmt.where(or_(Order.public_id == q.strip().upper(), Order.provider_payment_id == q.strip()))
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(Order.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
    rows, nxt = _page(rows, limit)
    return {"items": [ser.order(o, admin=True) for o in rows], "next_cursor": nxt}


@router.get("/orders/{order_id}")
async def get_order(
    order_id: Id, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        o = await session.get(Order, order_id)
        if o is None:
            raise NotFound("Заявка не найдена")
        user = await session.get(User, o.user_id)
    data = ser.order(o, admin=True)
    data["user"] = {"id": user.id, "tg_id": user.tg_id, "tg_username": user.tg_username} if user else None
    return data


@router.post("/orders/{order_id}/resolve-review")
async def resolve_review(
    order_id: Id,
    body: ResolveReviewIn,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    o = await orders.resolve_review(
        c, order_id, body.decision, staff_id=sa.staff.id, comment=body.comment, ip=client_ip(request)
    )
    return ser.order(o, admin=True)


@router.post("/orders/{order_id}/simulate-payment")
async def simulate_payment(
    order_id: Id,
    request: Request,
    body: SimulateIn | None = None,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    result = await orders.simulate_payment(
        c, order_id, amount_rub=body.amount_rub if body else None, staff_id=sa.staff.id, ip=client_ip(request)
    )
    async with c.db() as session:
        o = await session.get(Order, order_id)
    assert o is not None
    return {"callback_status": result.status, "result": result.result, "order": ser.order(o, admin=True)}


# --- users -------------------------------------------------------------------------------


class UserPatch(BaseModel):
    markup_pct: str | None = None
    order_min_rub: int | None = Field(None, ge=1, le=1_000_000_000)
    order_max_rub: int | None = Field(None, ge=1, le=1_000_000_000)
    daily_turnover_limit_rub: int | None = Field(None, ge=1, le=1_000_000_000)
    clear_limits: bool = False
    af_enabled: bool | None = None
    is_suspicious: bool | None = None
    status: Literal["active", "blocked"] | None = None
    comment: str | None = Field(None, max_length=1000)


class AdjustIn(BaseModel):
    amount: str = Field(max_length=40)
    comment: str = Field(max_length=1000)


@router.get("/users")
async def list_users(
    q: str | None = Query(None, max_length=100),
    filter_: Literal["suspicious", "blocked", "af_blocked"] | None = Query(None, alias="filter"),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    stmt = select(User).order_by(User.id.desc()).limit(limit + 1)
    if filter_ == "suspicious":
        stmt = stmt.where(User.is_suspicious.is_(True))
    elif filter_ == "blocked":
        stmt = stmt.where(User.status == UserStatus.blocked)
    elif filter_ == "af_blocked":
        stmt = stmt.where(User.can_create_orders.is_(False))
    if q:
        term = q.strip().lstrip("@")
        conds = [func.lower(User.tg_username) == term.lower()]
        if term.isdigit():
            conds += [User.tg_id == int(term), User.id == int(term)]
        if term.upper().startswith(("B-", "W-")):
            conds.append(User.id.in_(select(Order.user_id).where(Order.public_id == term.upper())))
            conds.append(User.id.in_(select(Withdrawal.user_id).where(Withdrawal.public_id == term.upper())))
        stmt = stmt.where(or_(*conds))
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(User.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
    rows, nxt = _page(rows, limit)
    return {"items": [ser.user_admin(u) for u in rows], "next_cursor": nxt}


@router.get("/users/{user_id}")
async def get_user(
    user_id: Id, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        u = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        available = await withdrawals.available_for_withdrawal(session, u, biz, c.clock.now())
        events = (
            await session.execute(
                select(AntifraudEvent)
                .where(AntifraudEvent.user_id == user_id)
                .order_by(AntifraudEvent.id.desc())
                .limit(50)
            )
        ).scalars()
        ev = [ser.af_event(e) for e in events]
    data = ser.user_admin(u)
    data["available_for_withdrawal"] = ser.money(available)
    data["antifraud_events"] = ev
    return data


@router.get("/users/{user_id}/stats")
async def user_stats(
    user_id: Id, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        u = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        return ser.client_stats(await stats.client_stats(session, u, biz))


@router.get("/users/{user_id}/stats/daily")
async def user_stats_daily(
    user_id: Id, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        await users_svc.get(session, user_id)
        days = await stats.client_daily(session, user_id)
    return {"items": [ser.day_stats(d) for d in days]}


@router.get("/users/{user_id}/ledger")
async def user_ledger(
    user_id: Id,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    stmt = select(LedgerEntry).where(LedgerEntry.user_id == user_id).order_by(LedgerEntry.id.desc()).limit(limit + 1)
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(LedgerEntry.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
    rows, nxt = _page(rows, limit)
    return {"items": [ser.ledger_entry(e) for e in rows], "next_cursor": nxt}


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: Id,
    body: UserPatch,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    async with c.db.begin() as session:
        u = await users_svc.lock(session, user_id)
        before = ser.user_admin(u)
        if body.markup_pct is not None:
            markup = _dec(body.markup_pct, "markup_pct", places=2, max_abs=Decimal(100))
            if not (Decimal(0) <= markup < Decimal(100)) or markup != markup.quantize(Decimal("0.01")):
                raise ServiceError("invalid_markup", "Наценка должна быть от 0 до 99.99 % с точностью 0.01.")
            u.markup_pct = markup
        if body.clear_limits:
            u.order_min_rub = u.order_max_rub = u.daily_turnover_limit_rub = None
        if body.order_min_rub is not None:
            u.order_min_rub = body.order_min_rub
        if body.order_max_rub is not None:
            u.order_max_rub = body.order_max_rub
        if body.daily_turnover_limit_rub is not None:
            u.daily_turnover_limit_rub = body.daily_turnover_limit_rub
        if u.order_min_rub and u.order_max_rub and u.order_min_rub > u.order_max_rub:
            raise ServiceError("invalid_limits", "Минимум не может быть больше максимума.")
        if body.af_enabled is not None:
            u.af_enabled = body.af_enabled
        if body.is_suspicious is not None:
            u.is_suspicious = body.is_suspicious
        if body.status is not None:
            if body.status == "blocked":
                u.status = UserStatus.blocked
            elif u.status == UserStatus.blocked:
                u.status = UserStatus.active if u.registered_at else UserStatus.registering
        after = ser.user_admin(u)
        changed = {k: v for k, v in after.items() if before.get(k) != v}
        await audit.write(
            session,
            action="user.update",
            staff_id=sa.staff.id,
            entity_type="user",
            entity_id=user_id,
            before={k: before[k] for k in changed},
            after=changed,
            comment=body.comment,
            ip=client_ip(request),
        )
    return after


@router.post("/users/{user_id}/unblock-orders")
async def unblock_orders(
    user_id: Id,
    body: CommentIn,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    return ser.user_admin(await antifraud.unblock(c, user_id, sa.actor, body.comment, client_ip(request)))


@router.post("/users/{user_id}/block-orders")
async def block_orders(
    user_id: Id,
    body: CommentIn,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    return ser.user_admin(await antifraud.block_orders(c, user_id, sa.actor, body.comment, client_ip(request)))


@router.post("/users/{user_id}/adjust-balance")
async def adjust_balance(
    user_id: Id,
    body: AdjustIn,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    delta = _dec(body.amount, "amount")
    now = c.clock.now()
    async with c.db.begin() as session:
        u = await users_svc.lock(session, user_id)
        before = {"balance": ser.money(u.balance)}
        entry = await ledger.adjust(session, u, delta, staff_id=sa.staff.id, comment=body.comment, now=now)
        await audit.write(
            session,
            action="user.adjust_balance",
            staff_id=sa.staff.id,
            entity_type="user",
            entity_id=user_id,
            before=before,
            after={"balance": ser.money(u.balance), "delta": ser.num(delta), "ledger_entry_id": entry.id},
            comment=body.comment,
            ip=client_ip(request),
        )
        who = f"@{escape_html(sa.staff.tg_username)}" if sa.staff.tg_username else f"staff #{sa.staff.id}"
        await notify.alert(
            session,
            f"✍️ Ручная корректировка баланса клиента id {user_id}: {ser.num(delta)} USDT ({who}). "
            f"Комментарий: {escape_html(body.comment)}",
            now=now,
        )
        return {"user": ser.user_admin(u), "entry": ser.ledger_entry(entry)}


# --- dashboard ---------------------------------------------------------------------------


@router.get("/dashboard")
async def dashboard(sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)) -> dict[str, Any]:
    async with c.db() as session:
        biz = await settings_svc.load(session)
        d = await stats.dashboard(session, biz, c.clock.now())
    return {
        "periods": {k: ser.period(p) for k, p in d.periods.items()},
        "queue": {"count": d.queue_count, "sum": ser.money(d.queue_sum)},
        "liabilities": {"balance": ser.money(d.liabilities_balance), "hold": ser.money(d.liabilities_hold)},
        "rate": (
            {"value": ser.rate_str(d.rate.value), "source": d.rate.source, "age_seconds": d.rate.age_seconds}
            if d.rate
            else None
        ),
        "last_rate_fetched_at": ser.ts(d.last_rate_fetched_at),
        "af_blocked": d.af_blocked,
        "suspicious": d.suspicious,
    }


@router.get("/dashboard/daily")
async def dashboard_daily(
    days: int = Query(30, ge=1, le=366), sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db() as session:
        rows = await stats.dashboard_daily(session, c.clock.now(), days)
    return {"items": [ser.day_stats(d) for d in rows]}


# --- antifraud -----------------------------------------------------------------------------


@router.get("/antifraud/events")
async def af_events(
    resolved: bool | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    stmt = select(AntifraudEvent).order_by(AntifraudEvent.id.desc()).limit(limit + 1)
    if resolved is True:
        stmt = stmt.where(AntifraudEvent.resolved_at.is_not(None))
    elif resolved is False:
        stmt = stmt.where(AntifraudEvent.resolved_at.is_(None))
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(AntifraudEvent.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
    rows, nxt = _page(rows, limit)
    return {"items": [ser.af_event(e) for e in rows], "next_cursor": nxt}


@router.post("/antifraud/events/{event_id}/resolve")
async def af_resolve(
    event_id: Id, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    return ser.af_event(await antifraud.resolve_event(c, event_id, sa.actor, client_ip(request)))


# --- settings ------------------------------------------------------------------------------


class SettingsPatch(BaseModel):
    changes: dict[str, Any]
    comment: str | None = Field(None, max_length=500)


@router.get("/settings")
async def get_settings(sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)) -> dict[str, Any]:
    async with c.db() as session:
        biz = await settings_svc.load(session)
    return {"values": settings_svc.dump(biz), "meta": settings_meta()}


@router.patch("/settings")
async def patch_settings(
    body: SettingsPatch, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    now = c.clock.now()
    async with c.db.begin() as session:
        biz, changes = await settings_svc.update(
            session, body.changes, staff_id=sa.staff.id, now=now, ip=client_ip(request), comment=body.comment
        )
        switches = [ch for ch in changes if ch.key in ("purchases_enabled", "withdrawals_enabled")]
        for ch in switches:
            what = "Покупки" if ch.key == "purchases_enabled" else "Выводы"
            await notify.alert(session, f"🔀 {what} {'включены' if ch.after else 'выключены'} админом.", now=now)
    return {"values": settings_svc.dump(biz), "changed": [ch.key for ch in changes]}


# --- rate ------------------------------------------------------------------------------------


class ManualRateIn(BaseModel):
    rate: str = Field(max_length=40)


async def _rate_view(c: Container) -> dict[str, Any]:
    async with c.db() as session:
        biz = await settings_svc.load(session)
        cur = await rates.current(session, biz, c.clock.now())
        last = await rates.latest(session)
        accepted = await rates.latest_accepted(session)
    return {
        "current": (
            {"value": ser.rate_str(cur.value), "source": cur.source, "age_seconds": cur.age_seconds} if cur else None
        ),
        "manual_rate": ser.rate_str(biz.manual_rate),
        "rate_field": biz.rate_field,
        "last_fetch": (
            {
                "value": ser.rate_str(last.value),
                "accepted": last.accepted,
                "reject_reason": last.reject_reason,
                "fetched_at": ser.ts(last.fetched_at),
                "ask": ser.rate_str(last.ask),
                "bid": ser.rate_str(last.bid),
            }
            if last
            else None
        ),
        "last_accepted": (
            {"value": ser.rate_str(accepted.value), "fetched_at": ser.ts(accepted.fetched_at)} if accepted else None
        ),
        "pending_jump": bool(last and not last.accepted and last.reject_reason == "jump"),
    }


@router.get("/rate")
async def get_rate(sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)) -> dict[str, Any]:
    return await _rate_view(c)


@router.post("/rate/manual")
async def set_manual_rate(
    body: ManualRateIn, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    value = _dec(body.rate, "rate")
    if value <= 0:
        raise ServiceError("invalid_rate", "Курс должен быть больше нуля.")
    async with c.db.begin() as session:
        await rates.set_manual(session, value, staff_id=sa.staff.id, now=c.clock.now(), ip=client_ip(request))
    return await _rate_view(c)


@router.delete("/rate/manual")
async def clear_manual_rate(
    request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db.begin() as session:
        await rates.set_manual(session, None, staff_id=sa.staff.id, now=c.clock.now(), ip=client_ip(request))
    return await _rate_view(c)


@router.post("/rate/accept-jump")
async def accept_jump(
    request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    async with c.db.begin() as session:
        await rates.accept_jump(session, staff_id=sa.staff.id, now=c.clock.now(), ip=client_ip(request))
    return await _rate_view(c)


# --- blacklist -------------------------------------------------------------------------------


class BlacklistIn(BaseModel):
    network: Network
    address: str = Field(max_length=200)
    comment: str | None = Field(None, max_length=500)


def _bl(row: AddressBlacklist) -> dict[str, Any]:
    return {
        "id": row.id,
        "network": row.network.value,
        "address": row.address,
        "comment": row.comment,
        "created_by": row.created_by,
        "created_at": ser.ts(row.created_at),
    }


@router.get("/blacklist")
async def list_blacklist(
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    stmt = select(AddressBlacklist).order_by(AddressBlacklist.id.desc()).limit(limit + 1)
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(AddressBlacklist.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
    rows, nxt = _page(rows, limit)
    return {"items": [_bl(r) for r in rows], "next_cursor": nxt}


@router.post("/blacklist")
async def add_blacklist(
    body: BlacklistIn, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    from app.chains.addresses import AddressError, validate_bep20, validate_trc20

    try:
        address = validate_trc20(body.address) if body.network == Network.TRC20 else validate_bep20(body.address)
    except AddressError as exc:
        raise ServiceError("invalid_address", exc.message) from None
    async with c.db.begin() as session:
        exists = (
            await session.execute(
                select(AddressBlacklist).where(
                    AddressBlacklist.network == body.network, func.lower(AddressBlacklist.address) == address.lower()
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise ServiceError("already_listed", "Адрес уже в чёрном списке.", status=409)
        row = AddressBlacklist(
            network=body.network,
            address=address,
            comment=body.comment,
            created_by=sa.staff.id,
            created_at=c.clock.now(),
        )
        session.add(row)
        await session.flush()
        await audit.write(
            session,
            action="blacklist.add",
            staff_id=sa.staff.id,
            entity_type="blacklist",
            entity_id=row.id,
            after=_bl(row),
            ip=client_ip(request),
        )
        return _bl(row)


@router.delete("/blacklist/{entry_id}")
async def delete_blacklist(
    entry_id: Id, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, bool]:
    async with c.db.begin() as session:
        row = await session.get(AddressBlacklist, entry_id)
        if row is None:
            raise NotFound("Запись не найдена")
        before = _bl(row)
        await session.delete(row)
        await audit.write(
            session,
            action="blacklist.remove",
            staff_id=sa.staff.id,
            entity_type="blacklist",
            entity_id=entry_id,
            before=before,
            ip=client_ip(request),
        )
    return {"ok": True}


# --- staff -----------------------------------------------------------------------------------


class StaffIn(BaseModel):
    tg_id: int | None = Field(None, gt=0)
    username: str | None = Field(None, max_length=64)
    role: StaffRole


class StaffPatch(BaseModel):
    role: StaffRole | None = None
    is_active: bool | None = None


@router.get("/staff")
async def list_staff(sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)) -> dict[str, Any]:
    async with c.db() as session:
        rows = (await session.execute(select(Staff).order_by(Staff.id))).scalars()
        return {"items": [ser.staff(s) for s in rows], "next_cursor": None}


@router.post("/staff")
async def add_staff(
    body: StaffIn, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    result = await staff_svc.add(
        c, sa.actor, role=body.role, tg_id=body.tg_id, username=body.username, ip=client_ip(request)
    )
    return {"staff": ser.staff(result.staff), "invited": result.invited}


@router.patch("/staff/{staff_id}")
async def patch_staff(
    staff_id: Id,
    body: StaffPatch,
    request: Request,
    sa: StaffAuth = Depends(admin_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    s = await staff_svc.update(c, sa.actor, staff_id, role=body.role, is_active=body.is_active, ip=client_ip(request))
    return ser.staff(s)


@router.delete("/staff/{staff_id}")
async def delete_staff(
    staff_id: Id, request: Request, sa: StaffAuth = Depends(admin_auth), c: Container = Depends(container)
) -> dict[str, bool]:
    await staff_svc.remove(c, sa.actor, staff_id, client_ip(request))
    return {"ok": True}


# --- profile ---------------------------------------------------------------------------------


class WebCredentialsIn(BaseModel):
    login: str = Field(max_length=64)
    password: str = Field(max_length=200)


class TotpConfirmIn(BaseModel):
    code: str = Field(max_length=10)


def _telegram_only(sa: StaffAuth) -> None:
    if sa.method != AuthMethod.telegram:
        raise Forbidden("Веб-доступ настраивается только в админке внутри Telegram")


@router.post("/profile/web-credentials")
async def web_credentials(
    body: WebCredentialsIn, request: Request, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    _telegram_only(sa)
    async with c.db.begin() as session:
        staff = await session.get(Staff, sa.staff.id, with_for_update=True)
        assert staff is not None
        await auth.set_web_credentials(session, staff, body.login, body.password, ip=client_ip(request))
        return ser.staff(staff)


@router.post("/profile/totp/setup")
async def totp_setup(sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)) -> dict[str, Any]:
    _telegram_only(sa)
    async with c.db.begin() as session:
        staff = await session.get(Staff, sa.staff.id, with_for_update=True)
        assert staff is not None
        setup = await auth.totp_setup(session, staff)
    return {"secret": setup.secret, "otpauth_uri": setup.otpauth_uri, "qr_png_base64": setup.qr_png_base64}


@router.post("/profile/totp/confirm")
async def totp_confirm(
    body: TotpConfirmIn, sa: StaffAuth = Depends(staff_auth), c: Container = Depends(container)
) -> dict[str, Any]:
    _telegram_only(sa)
    async with c.db.begin() as session:
        staff = await session.get(Staff, sa.staff.id, with_for_update=True)
        assert staff is not None
        await auth.totp_confirm(session, staff, body.code, c.clock.now())
        return ser.staff(staff)


# --- audit -----------------------------------------------------------------------------------


@router.get("/audit")
async def get_audit(
    staff_id: int | None = None,
    entity_type: str | None = Query(None, max_length=50),
    entity_id: str | None = Query(None, max_length=50),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    sa: StaffAuth = Depends(staff_auth),
    c: Container = Depends(container),
) -> dict[str, Any]:
    if sa.staff.role != StaffRole.admin:
        staff_id = sa.staff.id  # operators see only their own actions
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if staff_id is not None:
        stmt = stmt.where(AuditLog.staff_id == staff_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if (cur := _cursor(cursor)) is not None:
        stmt = stmt.where(AuditLog.id < cur)
    async with c.db() as session:
        rows = list((await session.execute(stmt)).scalars())
    rows, nxt = _page(rows, limit)
    return {"items": [ser.audit_entry(a) for a in rows], "next_cursor": nxt}
