"""Withdrawal flow in the bot."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from app.domain.amounts import AmountError, format_usdt, parse_usdt
from app.domain.enums import Network
from app.services import withdrawals
from app.services.labels import NETWORK
from app.telegram import keyboards, texts
from app.telegram.client import escape_html
from app.telegram.handlers.base import active_only, service_errors
from app.telegram.router import Ctx, Router

router = Router()

S_ADDRESS = "wd:address"
S_AMOUNT = "wd:amount"
S_CONFIRM = "wd:confirm"


@router.command("withdraw")
@router.text(texts.BTN_WITHDRAW)
@active_only
@service_errors
async def cmd_withdraw(ctx: Ctx) -> None:
    assert ctx.user is not None
    cfg = await withdrawals.start(ctx.container, ctx.user.id)
    rows = [
        [(texts.WD_NETWORK_BTN.format(name=NETWORK[n], fee=format_usdt(cfg.fees[n])), f"wd:net:{n.value}")]
        for n in Network
    ]
    rows.append([(texts.BUY_CANCEL, "nav:cancel")])
    await ctx.reply(
        texts.WD_CHOOSE_NETWORK.format(available=format_usdt(cfg.available), min=format_usdt(cfg.min_amount)),
        keyboards.inline(*rows),
    )


@router.callback("wd:net:")
@active_only
@service_errors
async def cb_network(ctx: Ctx) -> None:
    assert ctx.user is not None
    try:
        network = Network(ctx.cb_data.rsplit(":", 1)[1])
    except ValueError:
        await ctx.answer("Кнопка устарела")
        return
    await withdrawals.start(ctx.container, ctx.user.id)
    await ctx.answer()
    await ctx.fsm.set(S_ADDRESS, {"network": network.value})
    await ctx.edit(
        texts.WD_ASK_ADDRESS.format(network=NETWORK[network]), keyboards.inline([(texts.BUY_CANCEL, "nav:cancel")])
    )


async def _ask_amount(ctx: Ctx, network: Network) -> None:
    assert ctx.user is not None
    cfg = await withdrawals.get_config(ctx.container, ctx.user.id)
    await ctx.reply(
        texts.WD_ASK_AMOUNT.format(
            available=format_usdt(cfg.available), min=format_usdt(cfg.min_amount), fee=format_usdt(cfg.fees[network])
        ),
        keyboards.inline([(texts.WD_ALL, "wd:all")], [(texts.BUY_CANCEL, "nav:cancel")]),
    )


@router.state(S_ADDRESS)
@active_only
@service_errors
async def on_address(ctx: Ctx) -> None:
    network = Network(ctx.fsm.data["network"])
    address = await withdrawals.validate_address_for_user(ctx.container, network, ctx.text)
    await ctx.fsm.set(S_AMOUNT, {"network": network.value, "address": address})
    await _ask_amount(ctx, network)


async def _preview(ctx: Ctx, amount: Decimal) -> None:
    assert ctx.user is not None
    network = Network(ctx.fsm.data["network"])
    pv = await withdrawals.preview(ctx.container, ctx.user.id, network, ctx.fsm.data["address"], amount)
    sent = await ctx.reply(
        texts.WD_PREVIEW.format(
            network=NETWORK[network],
            address=escape_html(pv.address),
            amount=format_usdt(pv.amount),
            fee=format_usdt(pv.fee),
            to_send=format_usdt(pv.amount_to_send),
        ),
        keyboards.inline([(texts.BUY_CONFIRM, "wd:confirm"), (texts.BUY_CANCEL, "nav:cancel")]),
    )
    # The confirm button is valid only on this very preview message (an older preview must not confirm it).
    await ctx.fsm.set(
        S_CONFIRM,
        {
            "network": network.value,
            "address": pv.address,
            "amount": str(pv.amount),
            "preview_message_id": int(sent["message_id"]) if sent else None,
        },
    )


@router.state(S_AMOUNT)
@active_only
@service_errors
async def on_amount(ctx: Ctx) -> None:
    try:
        amount = parse_usdt(ctx.text)
    except AmountError as exc:
        await ctx.reply(exc.message)
        return
    await _preview(ctx, amount)


@router.callback("wd:all")
@active_only
@service_errors
async def cb_all(ctx: Ctx) -> None:
    assert ctx.user is not None
    if ctx.fsm.state != S_AMOUNT:
        await ctx.answer("Кнопка устарела")
        return
    await ctx.answer()
    cfg = await withdrawals.get_config(ctx.container, ctx.user.id)
    await _preview(ctx, cfg.available.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


@router.callback("wd:confirm")
@active_only
@service_errors
async def cb_confirm(ctx: Ctx) -> None:
    assert ctx.user is not None and ctx.callback is not None and ctx.callback.message is not None
    message_id = ctx.callback.message.message_id
    key = f"tg:{ctx.chat_id}:{message_id}"
    if ctx.fsm.state != S_CONFIRM or ctx.fsm.data.get("preview_message_id") != message_id:
        existing = await withdrawals.find_by_key(ctx.container, ctx.user.id, key)
        await ctx.answer(texts.WD_ALREADY if existing else "Кнопка устарела", alert=not existing)
        return
    data = ctx.fsm.data
    result = await withdrawals.create(
        ctx.container,
        ctx.user.id,
        Network(data["network"]),
        data["address"],
        Decimal(data["amount"]),
        idempotency_key=key,
    )
    await ctx.fsm.clear()
    if not result.created:
        await ctx.answer(texts.WD_ALREADY)
        return
    await ctx.answer()
    await ctx.tg.edit_message_reply_markup(ctx.chat_id, message_id)
    w = result.withdrawal
    await ctx.reply(
        texts.WD_CREATED.format(
            public_id=w.public_id, to_send=format_usdt(w.amount_to_send), network=NETWORK[w.network]
        ),
        keyboards.inline([(texts.WD_CANCEL_BTN, f"wd:cancel:{w.id}")]),
    )


@router.callback("wd:cancel:")
@active_only
@service_errors
async def cb_cancel(ctx: Ctx) -> None:
    assert ctx.user is not None
    try:
        wid = int(ctx.cb_data.rsplit(":", 1)[1])
    except ValueError:
        await ctx.answer("Кнопка устарела")
        return
    w = await withdrawals.cancel(ctx.container, ctx.user.id, wid)
    await ctx.answer("Вывод отменён")
    await ctx.edit(texts.WD_CANCELLED.format(public_id=w.public_id, amount=format_usdt(w.amount)))
