"""/start, registration, /help, /cancel, support, Mini App, chat membership."""

from __future__ import annotations

from sqlalchemy import update

from app.kyc.base import KycInput, KycStep
from app.models import User
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.telegram import keyboards, texts
from app.telegram.client import escape_html
from app.telegram.handlers.base import active_only, app_url
from app.telegram.router import Ctx, Router

router = Router()


async def show_main_menu(ctx: Ctx, text: str = texts.MAIN_MENU) -> None:
    await ctx.reply(text, keyboards.main_menu(app_url(ctx)))


async def _show_step(ctx: Ctx, step: KycStep) -> None:
    if step.status == "passed":
        await show_main_menu(ctx, texts.REGISTRATION_DONE)
    elif step.status == "rejected":
        await ctx.reply(step.message or texts.REGISTRATION_REJECTED)
    else:
        await ctx.fsm.set("kyc:input")
        await ctx.reply(step.message or "Ответьте на вопрос.")


@router.command("start")
async def cmd_start(ctx: Ctx) -> None:
    await ctx.fsm.clear()
    await ctx.container.on_start(ctx)
    assert ctx.user is not None
    if users_svc.is_active(ctx.user):
        await show_main_menu(ctx)
    else:
        await ctx.reply(texts.WELCOME_NEW, keyboards.register())


@router.callback("reg:start")
async def cb_register(ctx: Ctx) -> None:
    assert ctx.user is not None
    await ctx.answer()
    async with ctx.container.db.begin() as session:
        step = await users_svc.start_registration(session, ctx.container.kyc, ctx.user.id, ctx.container.clock.now())
    if ctx.callback is not None and ctx.callback.message is not None:
        await ctx.tg.edit_message_reply_markup(ctx.chat_id, ctx.callback.message.message_id)
    await _show_step(ctx, step)


@router.state("kyc:input")
async def on_kyc_input(ctx: Ctx) -> None:
    assert ctx.user is not None
    async with ctx.container.db.begin() as session:
        step = await users_svc.registration_input(
            session, ctx.container.kyc, ctx.user.id, KycInput(text=ctx.text), ctx.container.clock.now()
        )
    if step.status != "input_required":
        await ctx.fsm.clear()
    await _show_step(ctx, step)


@router.command("help")
async def cmd_help(ctx: Ctx) -> None:
    async with ctx.container.db() as session:
        biz = await settings_svc.load(session)
    await ctx.reply(texts.HELP.format(contact=escape_html(biz.support_contact)))


@router.command("cancel")
async def cmd_cancel(ctx: Ctx) -> None:
    await ctx.fsm.clear()
    assert ctx.user is not None
    if users_svc.is_active(ctx.user):
        await show_main_menu(ctx, texts.CANCELLED)
    else:
        await ctx.reply(texts.CANCELLED)


@router.callback("nav:cancel")
async def cb_cancel(ctx: Ctx) -> None:
    await ctx.fsm.clear()
    await ctx.answer()
    await ctx.edit(texts.CANCELLED)


@router.text(texts.BTN_SUPPORT)
@router.command("support")
async def cmd_support(ctx: Ctx) -> None:
    async with ctx.container.db() as session:
        biz = await settings_svc.load(session)
    await ctx.reply(texts.SUPPORT.format(contact=escape_html(biz.support_contact)))


@router.command("app")
@active_only
async def cmd_app(ctx: Ctx) -> None:
    await ctx.reply(texts.OPEN_APP, keyboards.web_app_button("📱 Открыть приложение", app_url(ctx)))


@router.chat_member()
async def on_chat_member(ctx: Ctx) -> None:
    member = ctx.update.my_chat_member
    if member is None:
        return
    blocked = member.new_chat_member.status in ("kicked", "left")
    async with ctx.container.db.begin() as session:
        await session.execute(update(User).where(User.tg_id == ctx.tg_id).values(bot_blocked=blocked))


@router.fallback()
async def fallback(ctx: Ctx) -> None:
    if ctx.callback is not None:
        await ctx.answer("Кнопка устарела")
        return
    assert ctx.user is not None
    if not users_svc.is_active(ctx.user):
        await ctx.reply(texts.REGISTRATION_REQUIRED, keyboards.register())
        return
    await show_main_menu(ctx, texts.UNKNOWN)
