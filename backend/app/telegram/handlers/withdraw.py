"""Withdrawal flow in the bot."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from app.domain.amounts import AmountError, format_usdt, parse_usdt
from app.domain.enums import Network
from app.services import address_book, withdrawals
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
    if len(cfg.fees) == 1:  # a single network: nothing to choose
        head = texts.WD_HEAD.format(available=format_usdt(cfg.available), min=format_usdt(cfg.min_amount))
        await _ask_address(ctx, next(iter(cfg.fees)), head=head)
        return
    rows = [
        [(texts.WD_NETWORK_BTN.format(name=NETWORK[n], fee=format_usdt(fee)), f"wd:net:{n.value}")]
        for n, fee in cfg.fees.items()
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
    withdrawals.ensure_network(ctx.container, network)
    await ctx.answer()
    await _ask_address(ctx, network, edit=True)


async def _ask_address(ctx: Ctx, network: Network, *, head: str = "", edit: bool = False) -> None:
    """Ask for the address; saved addresses of this network are offered as buttons."""
    assert ctx.user is not None
    async with ctx.container.db() as session:
        saved = await address_book.list_for(session, ctx.user.id, [network])
    await ctx.fsm.set(S_ADDRESS, {"network": network.value})
    text = head + texts.WD_ASK_ADDRESS.format(network=NETWORK[network])
    rows = [[(address_book.title(item), f"wd:book:{item.id}")] for item in saved]
    if rows:
        text += texts.WD_ASK_ADDRESS_BOOK
    markup = keyboards.inline(*rows, [(texts.BUY_CANCEL, "nav:cancel")])
    if edit:
        await ctx.edit(text, markup)
    else:
        await ctx.reply(text, markup)


@router.callback("wd:book:")
@active_only
@service_errors
async def cb_book(ctx: Ctx) -> None:
    """An address picked from the address book instead of typing it."""
    assert ctx.user is not None
    if ctx.fsm.state != S_ADDRESS:
        await ctx.answer("Кнопка устарела")
        return
    try:
        address_id = int(ctx.cb_data.rsplit(":", 1)[1])
    except ValueError:
        await ctx.answer("Кнопка устарела")
        return
    network = Network(ctx.fsm.data["network"])
    async with ctx.container.db() as session:
        item = await address_book.get(session, ctx.user.id, address_id)
    if item.network != network:
        await ctx.answer("Кнопка устарела")
        return
    address = await withdrawals.validate_address_for_user(ctx.container, network, item.address)
    await ctx.answer()
    await ctx.fsm.set(S_AMOUNT, {"network": network.value, "address": address})
    await _ask_amount(ctx, network)


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
    async with ctx.container.db() as session:
        known = await address_book.find(session, ctx.user.id, w.network, w.address)
    rows = [[(texts.WD_CANCEL_BTN, f"wd:cancel:{w.id}")]]
    if known is None:
        rows.append([(texts.WD_SAVE_BTN, f"wd:save:{w.id}")])
    await ctx.reply(
        texts.WD_CREATED.format(
            public_id=w.public_id, to_send=format_usdt(w.amount_to_send), network=NETWORK[w.network]
        ),
        keyboards.inline(*rows),
    )


@router.callback("wd:save:")
@active_only
@service_errors
async def cb_save_address(ctx: Ctx) -> None:
    """Save the address of the client's own withdrawal to the address book."""
    assert ctx.user is not None
    try:
        wid = int(ctx.cb_data.rsplit(":", 1)[1])
    except ValueError:
        await ctx.answer("Кнопка устарела")
        return
    async with ctx.container.db() as session:
        w = await withdrawals.get_for_user(session, ctx.user.id, wid)
    await address_book.add(ctx.container, ctx.user.id, w.network, w.address)
    await ctx.answer("Адрес сохранён")
    await ctx.reply(texts.WD_SAVED)


async def _show_book(ctx: Ctx) -> None:
    assert ctx.user is not None
    async with ctx.container.db() as session:
        saved = await address_book.list_for(session, ctx.user.id, ctx.container.config.withdraw_networks)
    if not saved:
        await ctx.reply(texts.BOOK_EMPTY)
        return
    lines = [
        f"{i}. {escape_html(item.label) + ' — ' if item.label else ''}<code>{escape_html(item.address)}</code>"
        f" ({NETWORK[item.network]})"
        for i, item in enumerate(saved, 1)
    ]
    rows = [[(texts.BOOK_DELETE_BTN.format(title=address_book.title(item)), f"ab:del:{item.id}")] for item in saved]
    await ctx.reply(texts.BOOK_LIST.format(items="\n".join(lines)), keyboards.inline(*rows))


@router.command("addresses")
@active_only
@service_errors
async def cmd_addresses(ctx: Ctx) -> None:
    await _show_book(ctx)


@router.callback("ab:del:")
@active_only
@service_errors
async def cb_delete_address(ctx: Ctx) -> None:
    assert ctx.user is not None
    try:
        address_id = int(ctx.cb_data.rsplit(":", 1)[1])
    except ValueError:
        await ctx.answer("Кнопка устарела")
        return
    await address_book.remove(ctx.container, ctx.user.id, address_id)
    await ctx.answer("Адрес удалён")
    await ctx.reply(texts.BOOK_DELETED)


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
