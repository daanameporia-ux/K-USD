"""Staff management: superadmins from env, add by Telegram ID or @username (invites), roles, deactivation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import StaffRole, SubjectType
from app.domain.errors import Conflict, Forbidden, NotFound, ServiceError
from app.models import AuditLog, Staff, User
from app.services import audit, auth, notify
from app.services.actor import Actor

if TYPE_CHECKING:
    from app.container import Container

INVITE_TTL = timedelta(hours=48)


def actor_of(staff: Staff) -> Actor:
    return Actor(staff.id, staff.role, staff.is_superadmin, staff.tg_id)


def _snapshot(s: Staff) -> dict[str, Any]:
    return {"role": s.role.value, "is_active": s.is_active, "tg_id": s.tg_id, "tg_username": s.tg_username}


def _label(s: Staff) -> str:
    if s.tg_username:
        return f"@{s.tg_username}"
    return f"tg {s.tg_id}" if s.tg_id else f"#{s.id}"


async def sync_superadmins(session: AsyncSession, tg_ids: list[int], now: datetime) -> None:
    for tg_id in tg_ids:
        row: Staff | None = (await session.execute(select(Staff).where(Staff.tg_id == tg_id))).scalar_one_or_none()
        if row is None:
            session.add(Staff(tg_id=tg_id, role=StaffRole.admin, is_active=True, is_superadmin=True, created_at=now))
        else:
            row.role = StaffRole.admin
            row.is_active = True
            row.is_superadmin = True
            row.invite_expires_at = None
    await session.flush()


async def active_by_tg(session: AsyncSession, tg_id: int) -> Staff | None:
    result: Staff | None = (
        await session.execute(select(Staff).where(Staff.tg_id == tg_id, Staff.is_active.is_(True)))
    ).scalar_one_or_none()
    return result


async def active_tg_ids(session: AsyncSession) -> list[int]:
    rows = await session.execute(select(Staff.tg_id).where(Staff.is_active.is_(True), Staff.tg_id.is_not(None)))
    return [int(r[0]) for r in rows if r[0] is not None]


async def get(session: AsyncSession, staff_id: int) -> Staff:
    staff = await session.get(Staff, staff_id)
    if staff is None:
        raise NotFound("Сотрудник не найден")
    return staff


@dataclass(frozen=True)
class AddResult:
    staff: Staff
    invited: bool


async def add(
    c: Container,
    actor: Actor,
    *,
    role: StaffRole,
    tg_id: int | None = None,
    username: str | None = None,
    ip: str | None = None,
) -> AddResult:
    actor.require_admin()
    role = StaffRole(role)
    now = c.clock.now()
    username = (username or "").strip().lstrip("@") or None
    if (tg_id is None) == (username is None):
        raise ServiceError("bad_request", "Укажите Telegram ID или @username.")
    async with c.db.begin() as session:
        if username is not None:
            user = (
                await session.execute(select(User).where(func.lower(User.tg_username) == username.lower()))
            ).scalar_one_or_none()
            if user is not None:
                tg_id = user.tg_id
        if tg_id is not None:
            existing = (await session.execute(select(Staff).where(Staff.tg_id == tg_id))).scalar_one_or_none()
            if existing is not None:
                if existing.is_active:
                    raise Conflict("staff_exists", "Этот пользователь уже сотрудник.")
                existing.is_active = True
                existing.role = role
                staff = existing
            else:
                staff = Staff(tg_id=tg_id, tg_username=username, role=role, invited_by=actor.id, created_at=now)
                session.add(staff)
            invited = False
        else:
            assert username is not None
            pending = (
                await session.execute(
                    select(Staff).where(Staff.tg_id.is_(None), func.lower(Staff.tg_username) == username.lower())
                )
            ).scalar_one_or_none()
            if pending is not None:
                pending.role = role
                pending.invite_expires_at = now + INVITE_TTL
                pending.invited_by = actor.id
                staff = pending
            else:
                staff = Staff(
                    tg_id=None,
                    tg_username=username,
                    role=role,
                    invited_by=actor.id,
                    invite_expires_at=now + INVITE_TTL,
                    created_at=now,
                )
                session.add(staff)
            invited = True
        await session.flush()
        await audit.write(
            session,
            action="staff.invite" if invited else "staff.add",
            staff_id=actor.id,
            entity_type="staff",
            entity_id=staff.id,
            after=_snapshot(staff),
            ip=ip,
        )
        what = "приглашён (ждём /start)" if invited else "добавлен"
        await notify.alert(session, f"👤 Сотрудник {_label(staff)} {what}, роль {role.value}.", now=now)
    if not invited and staff.tg_id is not None:
        from app.telegram.setup import set_staff_commands

        await set_staff_commands(c, staff.tg_id)
    return AddResult(staff, invited)


async def bind_invite(c: Container, tg_id: int, username: str | None) -> Staff | None:
    """Called on /start: bind a pending @username invitation to the real Telegram ID."""
    if not username:
        return None
    now = c.clock.now()
    async with c.db.begin() as session:
        invite = (
            await session.execute(
                select(Staff)
                .where(
                    Staff.tg_id.is_(None),
                    func.lower(Staff.tg_username) == username.lower(),
                    Staff.invite_expires_at > now,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if invite is None:
            return None
        taken = (await session.execute(select(Staff.id).where(Staff.tg_id == tg_id))).first()
        if taken is not None:
            return None
        invite.tg_id = tg_id
        invite.tg_username = username
        invite.invite_expires_at = None
        await audit.write(
            session, action="staff.invite_bound", entity_type="staff", entity_id=invite.id, after=_snapshot(invite)
        )
        if invite.invited_by is not None:
            inviter = await session.get(Staff, invite.invited_by)
            if inviter is not None and inviter.tg_id is not None:
                await notify.message(
                    session, inviter.tg_id, f"✅ @{username} принял приглашение и теперь сотрудник.", now=now
                )
        await notify.alert(session, f"👤 @{username} привязан как сотрудник ({invite.role.value}).", now=now)
    from app.telegram.setup import set_staff_commands

    await set_staff_commands(c, tg_id)
    return invite


async def update(
    c: Container,
    actor: Actor,
    staff_id: int,
    *,
    role: StaffRole | None = None,
    is_active: bool | None = None,
    ip: str | None = None,
) -> Staff:
    actor.require_admin()
    role = StaffRole(role) if role is not None else None
    now = c.clock.now()
    async with c.db.begin() as session:
        staff = await session.get(Staff, staff_id, with_for_update=True)
        if staff is None:
            raise NotFound("Сотрудник не найден")
        before = _snapshot(staff)
        if staff.id == actor.id and ((role is not None and role != staff.role) or is_active is False):
            raise Forbidden("Нельзя изменить собственную роль или деактивировать себя")
        if staff.is_superadmin and (role not in (None, StaffRole.admin) or is_active is False):
            raise Forbidden("Суперадмина нельзя понизить или деактивировать")
        if role is not None:
            staff.role = role
        if is_active is not None:
            staff.is_active = is_active
            if not is_active:
                await auth.revoke_all(session, SubjectType.staff, staff.id, now)
        await audit.write(
            session,
            action="staff.update",
            staff_id=actor.id,
            entity_type="staff",
            entity_id=staff.id,
            before=before,
            after=_snapshot(staff),
            ip=ip,
        )
        await notify.alert(session, f"👤 Сотрудник {_label(staff)} изменён: {before} → {_snapshot(staff)}", now=now)
        return staff


async def remove(c: Container, actor: Actor, staff_id: int, ip: str | None = None) -> None:
    """Pending invitations are deleted; real staff records are deactivated (they are referenced by the audit log)."""
    actor.require_admin()
    now = c.clock.now()
    async with c.db.begin() as session:
        staff = await session.get(Staff, staff_id, with_for_update=True)
        if staff is None:
            raise NotFound("Сотрудник не найден")
        if staff.is_superadmin:
            raise Forbidden("Суперадмина нельзя удалить")
        if staff.id == actor.id:
            raise Forbidden("Нельзя удалить себя")
        before = _snapshot(staff)
        referenced = (await session.execute(select(AuditLog.id).where(AuditLog.staff_id == staff.id).limit(1))).first()
        if staff.tg_id is None and referenced is None:
            await session.delete(staff)
        else:
            staff.is_active = False
            await auth.revoke_all(session, SubjectType.staff, staff.id, now)
        await audit.write(
            session,
            action="staff.remove",
            staff_id=actor.id,
            entity_type="staff",
            entity_id=staff_id,
            before=before,
            ip=ip,
        )
        await notify.alert(session, f"👤 Сотрудник {_label(staff)} удалён.", now=now)


async def expire_invites(c: Container) -> int:
    now = c.clock.now()
    async with c.db.begin() as session:
        result = await session.execute(
            delete(Staff).where(
                Staff.tg_id.is_(None),
                Staff.invite_expires_at.is_not(None),
                Staff.invite_expires_at < now,
                ~select(AuditLog.id).where(AuditLog.staff_id == Staff.id).exists(),
            )
        )
    return int(getattr(result, "rowcount", 0) or 0)
