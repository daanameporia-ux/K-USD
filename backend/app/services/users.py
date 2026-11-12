"""Clients: profile sync from Telegram, registration, locking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import KycStatus, UserStatus
from app.domain.errors import Forbidden, NotFound, ServiceError
from app.kyc.base import KycInput, KycProvider, KycStep
from app.models import User
from app.services import settings as settings_svc


@dataclass(frozen=True)
class TgProfile:
    tg_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


def is_active(user: User) -> bool:
    return user.status == UserStatus.active and user.kyc_status == KycStatus.passed


def ensure_active(user: User) -> None:
    if user.status == UserStatus.blocked:
        raise Forbidden("Ваш аккаунт заблокирован. Обратитесь в поддержку.", code="user_blocked")
    if not is_active(user):
        raise ServiceError("registration_required", "Сначала пройдите регистрацию.", status=403)


async def touch(session: AsyncSession, profile: TgProfile, now: datetime) -> tuple[User, bool]:
    """Create the user on first contact or refresh profile fields. Returns (user, created)."""
    biz = await settings_svc.load(session)
    values: dict[str, Any] = {
        "tg_username": profile.username,
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "language_code": profile.language_code,
        "last_seen_at": now,
    }
    stmt: Any = (
        insert(User)
        .values(
            tg_id=profile.tg_id,
            status=UserStatus.registering,
            kyc_status=KycStatus.none,
            kyc_data={},
            markup_pct=biz.default_markup_pct,
            created_at=now,
            **values,
        )
        .on_conflict_do_update(index_elements=[User.tg_id], set_={**values, "bot_blocked": False})
        .returning(User, literal_column("xmax = 0").label("created"))
    )
    row = (await session.execute(stmt.execution_options(populate_existing=True))).one()
    return row[0], bool(row[1])


async def get_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    result: User | None = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    return result


async def get(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("Клиент не найден")
    return user


async def lock(session: AsyncSession, user_id: int) -> User:
    """SELECT ... FOR UPDATE of the user row (always taken after order/withdrawal locks)."""
    stmt = select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    user: User | None = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFound("Клиент не найден")
    return user


def _refuse_blocked(user: User) -> None:
    """A blocked client must never get through registration: passing a step sets status back to active."""
    if user.status == UserStatus.blocked:
        raise Forbidden("Ваш аккаунт заблокирован. Обратитесь в поддержку.", code="user_blocked")


async def _apply_step(session: AsyncSession, user: User, step: KycStep, now: datetime) -> KycStep:
    if step.status == "passed":
        biz = await settings_svc.load(session)
        user.status = UserStatus.active
        user.kyc_status = KycStatus.passed
        user.markup_pct = biz.default_markup_pct
        user.registered_at = now
    elif step.status == "rejected":
        user.kyc_status = KycStatus.rejected
    if step.data:
        user.kyc_data = {**(user.kyc_data or {}), **step.data}
    await session.flush()
    return step


async def start_registration(session: AsyncSession, kyc: KycProvider, user_id: int, now: datetime) -> KycStep:
    user = await lock(session, user_id)
    _refuse_blocked(user)
    if is_active(user):
        return KycStep(status="passed")
    return await _apply_step(session, user, await kyc.start(user), now)


async def registration_input(
    session: AsyncSession, kyc: KycProvider, user_id: int, data: KycInput, now: datetime
) -> KycStep:
    user = await lock(session, user_id)
    _refuse_blocked(user)
    if is_active(user):
        return KycStep(status="passed")
    return await _apply_step(session, user, await kyc.handle_input(user, data), now)
