"""Purchase flow in the bot."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.domain.amounts import AmountError, format_rate, format_rub, format_usdt, parse_rub
from app.domain.enums import OrderSource, OrderStatus
from app.domain.errors import ServiceError
from app.domain.pricing import Quote
from app.models import Order
from app.services import messages, orders
from app.services.qr import qr_png
from app.telegram import keyboards, texts
from app.telegram.handlers.base import active_only, service_errors
from app.telegram.router import Ctx, Router

router = Router()

STATE_AMOUNT = "buy:amount"


def preview_text(q: Quote) -> str:
    return texts.BUY_PREVIEW.format(
        amount=format_rub(q.amount_rub),
        rate=format_rate(q.rate),
        markup=f"{q.markup_pct.normalize():f}",
        margin=format_rub(q.margin_rub),
        usdt=format_usdt(q.usdt_amount),
    )


def preview_markup(q: Quote) -> dict[str, object]:
    return keyboards.inline(
        [(texts.BUY_CONFIRM, f"buy:confirm:{q.amount_rub}:{q.usdt_amount}"), (texts.BUY_CANCEL, "nav:cancel")]
    )


def order_markup(order: Order) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [keyboards.button_url(texts.BUY_PAY, order.qr_url or "")],
            [keyboards.button_cb(texts.BUY_CANCEL_ORDER, f"ord:cancel:{order.id}")],
        ]
    }


async def send_order_qr(ctx: Ctx, order: Order) -> None:
    caption = messages.order_caption(order.public_id, order.amount_rub, order.usdt_amount, order.expires_at)
    sent = await ctx.tg.send_photo(ctx.chat_id, qr_png(order.qr_url or ""), caption, order_markup(order))
    if sent is not None:
        await orders.attach_message(ctx.container, order.id, ctx.chat_id, int(sent["message_id"]))


@router.command("buy")
@router.text(texts.BTN_BUY)
@active_only
@service_errors
async def cmd_buy(ctx: Ctx) -> None:
    assert ctx.user is not None
    try:
        info = await orders.start(ctx.container, ctx.user.id)
    except orders.ActiveOrderExists as exc:
        order = exc.order
        if order.status == OrderStatus.pending and order.qr_url:
            await ctx.reply(texts.BUY_ACTIVE_ORDER)
            await send_order_qr(ctx, order)
        else:
            await ctx.reply(exc.message)
        return
    await ctx.fsm.set(STATE_AMOUNT)
    await ctx.reply(
        texts.BUY_ASK_AMOUNT.format(min=format_rub(info.limits.min_rub), max=format_rub(info.limits.max_rub)),
        keyboards.inline([(texts.BUY_CANCEL, "nav:cancel")]),
    )


@router.state(STATE_AMOUNT)
@active_only
@service_errors
async def on_amount(ctx: Ctx) -> None:
    assert ctx.user is not None
    try:
        info = await orders.start(ctx.container, ctx.user.id)
        amount = parse_rub(ctx.text, info.limits.min_rub, info.limits.max_rub)
    except AmountError as exc:
        await ctx.reply(exc.message)  # state is kept, the user can try again
        return
    except ServiceError:
        await ctx.fsm.clear()
        raise
    q = await orders.preview(ctx.container, ctx.user.id, amount)
    await ctx.fsm.clear()
    await ctx.reply(preview_text(q), preview_markup(q))


@router.callback("buy:confirm:")
@active_only
@service_errors
async def cb_confirm(ctx: Ctx) -> None:
    assert ctx.user is not None and ctx.callback is not None and ctx.callback.message is not None
    try:
        _, _, amount_s, usdt_s = ctx.cb_data.split(":", 3)
        amount, expected = int(amount_s), Decimal(usdt_s)
    except (ValueError, InvalidOperation):
        await ctx.answer("Кнопка устарела")
        return
    message_id = ctx.callback.message.message_id
    try:
        result = await orders.create(
            ctx.container,
            ctx.user.id,
            amount,
            source=OrderSource.bot,
            idempotency_key=f"tg:{ctx.chat_id}:{message_id}",
            expected_usdt=expected,
            tg_chat_id=ctx.chat_id,
        )
    except orders.PriceChanged as exc:
        await ctx.answer()
        await ctx.edit(texts.BUY_PRICE_CHANGED + preview_text(exc.quote), preview_markup(exc.quote))
        return
    if not result.created:
        await ctx.answer(texts.BUY_ALREADY_CREATED)
        return
    await ctx.answer()
    await ctx.tg.edit_message_reply_markup(ctx.chat_id, message_id)
    order = result.order
    if order.status != OrderStatus.pending:
        await ctx.reply(orders.MSG_PROVIDER_FAILED)
        return
    await send_order_qr(ctx, order)


@router.callback("ord:cancel:")
@active_only
@service_errors
async def cb_cancel_order(ctx: Ctx) -> None:
    assert ctx.user is not None
    try:
        order_id = int(ctx.cb_data.rsplit(":", 1)[1])
    except ValueError:
        await ctx.answer("Кнопка устарела")
        return
    order = await orders.cancel(ctx.container, ctx.user.id, order_id)
    await ctx.answer("Заявка отменена")
    await ctx.reply(messages.order_cancelled(order.public_id))
