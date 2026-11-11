from __future__ import annotations

from app.domain.amounts import format_usdt
from app.services import ledger
from app.services import users as users_svc
from app.telegram import texts
from app.telegram.handlers.base import active_only
from app.telegram.router import Ctx, Router

router = Router()


@router.command("balance")
@router.text(texts.BTN_BALANCE)
@active_only
async def cmd_balance(ctx: Ctx) -> None:
    assert ctx.user is not None
    async with ctx.container.db() as session:
        user = await users_svc.get(session, ctx.user.id)
        info = await ledger.summary(session, user)
    await ctx.reply(
        texts.BALANCE.format(
            available=format_usdt(info.available),
            hold=format_usdt(info.hold),
            bought=format_usdt(info.total_bought),
            withdrawn=format_usdt(info.total_withdrawn),
        )
    )
