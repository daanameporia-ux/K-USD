from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api import serializers as ser
from app.api.deps import bearer, client_ip, container, limit
from app.container import Container
from app.domain import initdata
from app.domain.enums import AuthMethod, SubjectType
from app.domain.errors import Forbidden, ServiceError
from app.services import auth, users
from app.services import staff as staff_svc

router = APIRouter(prefix="/api/auth")


class TelegramAuthIn(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)
    scope: Literal["client", "admin"]


class PasswordAuthIn(BaseModel):
    login: str = Field(max_length=100)
    password: str = Field(max_length=200)
    totp: str = Field(max_length=10)


@router.post("/telegram")
async def telegram_auth(body: TelegramAuthIn, request: Request, c: Container = Depends(container)) -> dict[str, object]:
    ip = client_ip(request)
    limit(c, f"auth-ip:{ip}", 60)
    now = c.clock.now()
    try:
        tg = initdata.verify(body.init_data, c.config.bot_token, now)
    except initdata.InitDataError:
        raise auth.Unauthorized("Не удалось подтвердить данные Telegram. Откройте приложение заново.") from None
    ua = request.headers.get("user-agent")
    async with c.db.begin() as session:
        if body.scope == "client":
            profile = users.TgProfile(tg.id, tg.username, tg.first_name, tg.last_name, tg.language_code)
            user, _ = await users.touch(session, profile, now)
            token = await auth.create_session(
                session,
                secret=c.config.session_secret,
                subject_type=SubjectType.user,
                subject_id=user.id,
                method=AuthMethod.telegram,
                now=now,
                ip=ip,
                user_agent=ua,
            )
            return {"token": token, "subject": "user", "user_id": user.id}
        staff = await staff_svc.active_by_tg(session, tg.id)
        if staff is None:
            raise Forbidden("Нет доступа к админке")
        staff.tg_username = tg.username or staff.tg_username
        staff.last_login_at = now
        token = await auth.create_session(
            session,
            secret=c.config.session_secret,
            subject_type=SubjectType.staff,
            subject_id=staff.id,
            method=AuthMethod.telegram,
            now=now,
            ip=ip,
            user_agent=ua,
        )
        return {"token": token, "subject": "staff", "staff": ser.staff(staff)}


@router.post("/password")
async def password_auth(body: PasswordAuthIn, request: Request, c: Container = Depends(container)) -> dict[str, object]:
    ip = client_ip(request)
    async with c.db.begin() as session:
        result = await auth.password_login(
            session,
            login=body.login,
            password=body.password,
            totp_code=body.totp,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
            secret=c.config.session_secret,
            now=c.clock.now(),
        )
    if result is None:
        raise ServiceError("bad_credentials", auth.MSG_BAD_LOGIN, status=401)
    return {"token": result.token, "subject": "staff", "staff": ser.staff(result.staff)}


@router.post("/logout")
async def logout(request: Request, c: Container = Depends(container)) -> dict[str, bool]:
    token = bearer(request)
    async with c.db.begin() as session:
        await auth.revoke(session, token, secret=c.config.session_secret, now=c.clock.now())
    return {"ok": True}
