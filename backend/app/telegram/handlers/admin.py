from __future__ import annotations

from app.services import staff as staff_svc
from app.telegram import keyboards, texts
from app.telegram.handlers.common import fallback
from app.telegram.router import Ctx, Router

router = Router()


@router.command("admin")
async def cmd_admin(ctx: Ctx) -> None:
    async with ctx.container.db() as session:
        staff = await staff_svc.active_by_tg(session, ctx.tg_id)
    if staff is None:
        await fallback(ctx)  # behave as for an unknown command
        return
    url = f"{ctx.container.config.public_base_url}/admin/"
    await ctx.reply(texts.ADMIN_OPEN, keyboards.web_app_button("🛠 Открыть админку", url))
