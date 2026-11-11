"""Request dependencies: container, client IP, authentication, roles, rate limiting."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, Request

from app.domain.enums import AuthMethod, StaffRole, SubjectType
from app.domain.errors import Forbidden, ServiceError
from app.models import Staff, User
from app.services import auth
from app.services.actor import Actor

if TYPE_CHECKING:
    from app.container import Container

SESSION_LIMIT = 120  # requests per minute per session
MONEY_LIMIT = 10  # POST /orders, /withdrawals per minute per user


class RateLimiter:
    """Sliding-window limiter living in the process (single-instance service)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str, limit: int, window: float, now: float) -> bool:
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 50_000:  # keep memory bounded
            for k in [k for k, v in self._hits.items() if not v][:10_000]:
                del self._hits[k]
        return True


class TooManyRequests(ServiceError):
    status = 429

    def __init__(self) -> None:
        super().__init__("rate_limited", "Слишком много запросов, повторите через минуту.")


def container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise auth.Unauthorized()
    return token.strip()


def limit(c: Container, key: str, max_hits: int, window: float = 60.0) -> None:
    if not c.limiter.hit(key, max_hits, window, c.clock.now().timestamp()):
        raise TooManyRequests()


@dataclass
class ClientAuth:
    session_id: int
    user: User
    token: str


@dataclass
class StaffAuth:
    session_id: int
    staff: Staff
    method: AuthMethod
    token: str

    @property
    def actor(self) -> Actor:
        return Actor(self.staff.id, self.staff.role, self.staff.is_superadmin, self.staff.tg_id)


async def client_auth(request: Request, c: Container = Depends(container)) -> ClientAuth:
    token = bearer(request)
    async with c.db.begin() as session:
        sess = await auth.resolve(session, token, secret=c.config.session_secret, now=c.clock.now())
        if sess.subject_type != SubjectType.user:
            raise Forbidden("Доступно только клиентам")
        user = await session.get(User, sess.subject_id)
        if user is None:
            raise auth.Unauthorized()
    limit(c, f"s:{sess.id}", SESSION_LIMIT)
    return ClientAuth(sess.id, user, token)


async def staff_auth(request: Request, c: Container = Depends(container)) -> StaffAuth:
    token = bearer(request)
    async with c.db.begin() as session:
        sess = await auth.resolve(session, token, secret=c.config.session_secret, now=c.clock.now())
        if sess.subject_type != SubjectType.staff:
            raise Forbidden("Доступ только для сотрудников")
        staff = await session.get(Staff, sess.subject_id)
        if staff is None or not staff.is_active:
            sess.revoked_at = c.clock.now()
            raise auth.Unauthorized("Доступ отозван", code="access_revoked")
    limit(c, f"s:{sess.id}", SESSION_LIMIT)
    return StaffAuth(sess.id, staff, sess.auth_method, token)


async def admin_auth(sa: StaffAuth = Depends(staff_auth)) -> StaffAuth:
    if sa.staff.role != StaffRole.admin:
        raise Forbidden("Действие доступно только админу")
    return sa


def idempotency_key(request: Request) -> str:
    key = (request.headers.get("idempotency-key") or "").strip()
    if not (8 <= len(key) <= 100):
        raise ServiceError("idempotency_key_required", "Нужен заголовок Idempotency-Key.")
    return key
