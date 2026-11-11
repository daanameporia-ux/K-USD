"""Shared helpers for handlers: user loading and access gate."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable

from app.domain.enums import UserStatus
from app.domain.errors import ServiceError
from app.services import users as users_svc
from app.telegram import keyboards, texts
from app.telegram.router import Ctx, Handler


def app_url(ctx: Ctx) -> str:
    return f"{ctx.container.config.public_base_url}/app/"


async def load_user(ctx: Ctx) -> bool:
    """Before-hook: sync profile and attach ctx.user. Returns False to stop processing."""
    tg_user = ctx.update.from_user
    if tg_user is None:
        return False
    profile = users_svc.TgProfile(
        tg_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        language_code=tg_user.language_code,
    )
    async with ctx.container.db.begin() as session:
        user, created = await users_svc.touch(session, profile, ctx.container.clock.now())
    ctx.user = user
    ctx.extra["user_created"] = created
    if ctx.update.my_chat_member is not None:
        return True
    if user.status == UserStatus.blocked:
        await ctx.answer()
        await ctx.reply(texts.BLOCKED, keyboards.REMOVE)
        return False
    return True


def active_only(fn: Handler) -> Handler:
    """Handler decorator: only registered, active clients pass."""

    @functools.wraps(fn)
    async def wrapper(ctx: Ctx) -> None:
        user = ctx.user
        if user is None or not users_svc.is_active(user):
            await ctx.answer()
            await ctx.reply(texts.REGISTRATION_REQUIRED, keyboards.register())
            return
        await fn(ctx)

    return wrapper


def service_errors(fn: Callable[[Ctx], Awaitable[None]]) -> Handler:
    """Show ServiceError messages to the user instead of the generic error text."""

    @functools.wraps(fn)
    async def wrapper(ctx: Ctx) -> None:
        try:
            await fn(ctx)
        except ServiceError as exc:
            if ctx.callback is not None:
                await ctx.answer(exc.message[:190], alert=True)
            else:
                await ctx.reply(exc.message)

    return wrapper
