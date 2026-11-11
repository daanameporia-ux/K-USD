"""Sessions (opaque bearer tokens), password + TOTP login, brute-force protection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AuthMethod, StaffRole, SubjectType
from app.domain.errors import Forbidden, ServiceError
from app.models import AuthSession, LoginAttempt, Staff
from app.services import audit

CLIENT_TTL = timedelta(hours=12)
STAFF_TTL = timedelta(hours=8)
STAFF_IDLE = timedelta(minutes=60)
BRUTE_WINDOW = timedelta(minutes=15)
BRUTE_LIMIT = 5
PASSWORD_MIN = 12
TOTP_ISSUER = "K-USD admin"

_hasher = PasswordHasher()  # argon2id by default

MSG_BAD_LOGIN = "Неверный логин, пароль или код."
MSG_TOO_MANY = "Слишком много неудачных попыток. Попробуйте через 15 минут."


class Unauthorized(ServiceError):
    status = 401

    def __init__(self, message: str = "Требуется авторизация", code: str = "unauthorized") -> None:
        super().__init__(code, message)


def hash_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


async def create_session(
    session: AsyncSession,
    *,
    secret: str,
    subject_type: SubjectType,
    subject_id: int,
    method: AuthMethod,
    now: datetime,
    ip: str | None,
    user_agent: str | None,
) -> str:
    token = new_token()
    ttl = CLIENT_TTL if subject_type == SubjectType.user else STAFF_TTL
    session.add(
        AuthSession(
            token_hash=hash_token(token, secret),
            subject_type=subject_type,
            subject_id=subject_id,
            auth_method=method,
            created_at=now,
            last_used_at=now,
            expires_at=now + ttl,
            ip=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    await session.flush()
    return token


async def resolve(session: AsyncSession, token: str, *, secret: str, now: datetime) -> AuthSession:
    """Validate a token, apply expiry rules and refresh activity. Raises Unauthorized."""
    if not token:
        raise Unauthorized()
    row: AuthSession | None = (
        await session.execute(
            select(AuthSession).where(AuthSession.token_hash == hash_token(token, secret)).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise Unauthorized("Сессия истекла, войдите снова", code="session_expired")
    if row.subject_type == SubjectType.staff:
        if now - row.last_used_at > STAFF_IDLE:
            raise Unauthorized("Сессия истекла из-за неактивности", code="session_expired")
    else:
        row.expires_at = max(row.expires_at, now + CLIENT_TTL)  # sliding expiry for clients
    row.last_used_at = now
    return row


async def revoke(session: AsyncSession, token: str, *, secret: str, now: datetime) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == hash_token(token, secret), AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_all(session: AsyncSession, subject_type: SubjectType, subject_id: int, now: datetime) -> None:
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.subject_type == subject_type,
            AuthSession.subject_id == subject_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def cleanup(session: AsyncSession, now: datetime) -> int:
    result = await session.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=1)))
    await session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now - timedelta(days=1)))
    return int(getattr(result, "rowcount", 0) or 0)


# --- password + TOTP -----------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password(stored: str | None, password: str) -> bool:
    if not stored:
        # constant-ish time for unknown logins
        with suppress(VerificationError, InvalidHashError):
            _hasher.verify(_DUMMY_HASH, password)
        return False
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def totp_step(secret: str, code: str, now: datetime) -> int | None:
    """Return the matching time step for the code (±1 step), or None."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    for offset in (0, -1, 1):
        moment = now + timedelta(seconds=30 * offset)
        if hmac.compare_digest(totp.at(moment), code):
            return int(totp.timecode(moment))
    return None


async def _too_many_failures(session: AsyncSession, login: str, ip: str, now: datetime) -> bool:
    since = now - BRUTE_WINDOW
    row = (
        await session.execute(
            select(
                func.count(LoginAttempt.id).filter(LoginAttempt.login == login),
                func.count(LoginAttempt.id).filter(LoginAttempt.ip == ip),
            ).where(LoginAttempt.created_at > since, or_(LoginAttempt.login == login, LoginAttempt.ip == ip))
        )
    ).one()
    return int(row[0]) >= BRUTE_LIMIT or int(row[1]) >= BRUTE_LIMIT


@dataclass(frozen=True)
class LoginResult:
    token: str
    staff: Staff


async def password_login(
    session: AsyncSession,
    *,
    login: str,
    password: str,
    totp_code: str,
    ip: str,
    user_agent: str | None,
    secret: str,
    now: datetime,
) -> LoginResult | None:
    """Returns None on bad credentials (the caller commits the failure record and answers generically)."""
    login_norm = (login or "").strip().lower()
    if await _too_many_failures(session, login_norm, ip, now):
        raise ServiceError("too_many_attempts", MSG_TOO_MANY, status=429)
    staff: Staff | None = (
        await session.execute(select(Staff).where(func.lower(Staff.login) == login_norm).with_for_update())
    ).scalar_one_or_none()
    ok = check_password(staff.password_hash if staff else None, password)
    if staff is None or not ok or not staff.is_active or not staff.totp_enabled or not staff.totp_secret:
        session.add(LoginAttempt(login=login_norm, ip=ip, created_at=now))
        return None
    step = totp_step(staff.totp_secret, totp_code, now)
    if step is None or (staff.totp_last_step is not None and step <= staff.totp_last_step):
        session.add(LoginAttempt(login=login_norm, ip=ip, created_at=now))
        return None
    staff.totp_last_step = step
    staff.last_login_at = now
    await session.execute(delete(LoginAttempt).where(LoginAttempt.login == login_norm))
    token = await create_session(
        session,
        secret=secret,
        subject_type=SubjectType.staff,
        subject_id=staff.id,
        method=AuthMethod.password,
        now=now,
        ip=ip,
        user_agent=user_agent,
    )
    await audit.write(
        session, action="staff.login_password", staff_id=staff.id, entity_type="staff", entity_id=staff.id, ip=ip
    )
    return LoginResult(token, staff)


async def set_web_credentials(
    session: AsyncSession, staff: Staff, login: str, password: str, *, ip: str | None
) -> None:
    login = (login or "").strip()
    if not (3 <= len(login) <= 64) or not all(ch.isalnum() or ch in "._-" for ch in login):
        raise ServiceError("invalid_login", "Логин: 3–64 символа, латиница, цифры, точка, дефис, подчёркивание.")
    if len(password or "") < PASSWORD_MIN:
        raise ServiceError("weak_password", f"Пароль должен быть не короче {PASSWORD_MIN} символов.")
    taken = (
        await session.execute(select(Staff.id).where(func.lower(Staff.login) == login.lower(), Staff.id != staff.id))
    ).first()
    if taken is not None:
        raise ServiceError("login_taken", "Этот логин уже занят.", status=409)
    staff.login = login
    staff.password_hash = hash_password(password)
    await audit.write(
        session,
        action="staff.set_web_credentials",
        staff_id=staff.id,
        entity_type="staff",
        entity_id=staff.id,
        after={"login": login},
        ip=ip,
    )


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    otpauth_uri: str
    qr_png_base64: str


async def totp_setup(session: AsyncSession, staff: Staff) -> TotpSetup:
    secret = pyotp.random_base32()
    staff.totp_secret = secret
    staff.totp_enabled = False
    staff.totp_last_step = None
    name = staff.login or (f"@{staff.tg_username}" if staff.tg_username else f"staff-{staff.id}")
    uri = pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name=TOTP_ISSUER)
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="png", scale=6, border=2)
    await audit.write(session, action="staff.totp_setup", staff_id=staff.id, entity_type="staff", entity_id=staff.id)
    return TotpSetup(secret, uri, base64.b64encode(buf.getvalue()).decode())


async def totp_confirm(session: AsyncSession, staff: Staff, code: str, now: datetime) -> None:
    if not staff.totp_secret:
        raise ServiceError("totp_not_setup", "Сначала получите секрет TOTP.")
    step = totp_step(staff.totp_secret, code, now)
    if step is None:
        raise ServiceError("totp_invalid", "Неверный код. Проверьте время на телефоне и попробуйте снова.")
    staff.totp_enabled = True
    staff.totp_last_step = step
    await audit.write(session, action="staff.totp_enabled", staff_id=staff.id, entity_type="staff", entity_id=staff.id)


def require_role(role: StaffRole, staff_role: StaffRole) -> None:
    if role == StaffRole.admin and staff_role != StaffRole.admin:
        raise Forbidden()
