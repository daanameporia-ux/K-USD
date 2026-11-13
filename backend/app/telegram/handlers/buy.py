"""Purchase flow in the bot."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.amounts import AmountError, format_rate, format_rub, format_usdt, parse_rub
from app.domain.buyer import PROOF_MAX_BYTES
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
STATE_PHONE = "buy:phone"
STATE_NAME = "buy:name"
STATE_CONFIRM = "buy:confirm"  # keeps the buyer details until the confirm button is pressed


def preview_text(q: Quote, buyer: dict[str, Any] | None = None) -> str:
    text = texts.BUY_PREVIEW.format(
        amount=format_rub(q.amount_rub),
        rate=format_rate(q.rate),
        markup=f"{q.markup_pct.normalize():f}",
        margin=format_rub(q.margin_rub),
        usdt=format_usdt(q.usdt_amount),
    )
    if buyer and buyer.get("phone"):
        text += texts.BUY_PREVIEW_PHONE.format(phone=buyer["phone"])
    if buyer and buyer.get("name"):
        text += texts.BUY_PREVIEW_NAME.format(name=buyer["name"])
    return text


async def _next_step(ctx: Ctx, data: dict[str, Any]) -> None:
    """Ask for whatever this client still has to provide, then show the preview."""
    assert ctx.user is not None
    cancel = keyboards.inline([(texts.BUY_CANCEL, "nav:cancel")])
    if ctx.user.require_buyer_phone and not data.get("phone"):
        await ctx.fsm.set(STATE_PHONE, data)
        await ctx.reply(texts.BUY_ASK_PHONE, cancel)
        return
    if ctx.user.require_buyer_name and not data.get("name"):
        await ctx.fsm.set(STATE_NAME, data)
        await ctx.reply(texts.BUY_ASK_NAME, cancel)
        return
    try:
        q = await orders.preview(ctx.container, ctx.user.id, int(data["amount"]))
    except ServiceError:
        await ctx.fsm.clear()
        raise
    await ctx.fsm.set(STATE_CONFIRM, data)
    await ctx.reply(preview_text(q, data), preview_markup(q))


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
    except orders.ProofRequired as exc:
        await ctx.reply(texts.PROOF_ASK.format(public_id=exc.order.public_id, hint=messages.PROOF_HINT))
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
    await orders.preview(ctx.container, ctx.user.id, amount)  # amount-dependent checks before asking more
    await _next_step(ctx, {"amount": amount})


@router.state(STATE_PHONE)
@active_only
@service_errors
async def on_phone(ctx: Ctx) -> None:
    phone = orders.buyer_phone(ctx.text)  # on error the state is kept, the user can try again
    await _next_step(ctx, {**ctx.fsm.data, "phone": phone})


@router.state(STATE_NAME)
@active_only
@service_errors
async def on_name(ctx: Ctx) -> None:
    name = orders.buyer_name(ctx.text)
    await _next_step(ctx, {**ctx.fsm.data, "name": name})


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
    buyer = ctx.fsm.data if ctx.state == STATE_CONFIRM and ctx.fsm.data.get("amount") == amount else {}
    if (ctx.user.require_buyer_phone and not buyer.get("phone")) or (
        ctx.user.require_buyer_name and not buyer.get("name")
    ):
        await ctx.answer("Кнопка устарела")
        await ctx.reply(texts.BUY_STALE)
        return
    await ctx.answer("Создаём заявку…")  # answer before the (possibly slow) provider call
    try:
        result = await orders.create(
            ctx.container,
            ctx.user.id,
            amount,
            source=OrderSource.bot,
            idempotency_key=f"tg:{ctx.chat_id}:{message_id}",
            expected_usdt=expected,
            tg_chat_id=ctx.chat_id,
            buyer_phone=buyer.get("phone"),
            buyer_name=buyer.get("name"),
        )
    except orders.PriceChanged as exc:
        await ctx.edit(texts.BUY_PRICE_CHANGED + preview_text(exc.quote, buyer), preview_markup(exc.quote))
        return
    await ctx.fsm.clear()
    if not result.created:
        await ctx.reply(texts.BUY_ALREADY_CREATED)
        return
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


@router.media()
@active_only
@service_errors
async def on_media(ctx: Ctx) -> None:
    """A photo or a file sent to the chat is the payment screenshot of the paid order that waits for one."""
    assert ctx.user is not None and ctx.message is not None
    async with ctx.container.db() as session:
        order = await orders.pending_proof(session, ctx.user)
    if order is None:
        await ctx.reply(texts.PROOF_NOT_EXPECTED)
        return
    msg = ctx.message
    if msg.photo:
        best = msg.photo[-1]  # sizes go from the smallest to the largest
        file_id, size = best.file_id, best.file_size
    else:
        assert msg.document is not None
        file_id, size = msg.document.file_id, msg.document.file_size
    data = None if (size or 0) > PROOF_MAX_BYTES else await ctx.tg.download_file(file_id, PROOF_MAX_BYTES)
    if data is None:
        await ctx.reply(texts.PROOF_TOO_LARGE)
        return
    saved = await orders.attach_proof(ctx.container, ctx.user.id, order.id, data, source=OrderSource.bot)
    await ctx.reply(messages.proof_saved(saved.public_id))
