from __future__ import annotations

from app.domain.amounts import format_rub, format_usdt
from app.domain.timefmt import fmt_dt
from app.services import history
from app.services.labels import NETWORK
from app.telegram import keyboards, texts
from app.telegram.handlers.base import active_only
from app.telegram.router import Ctx, Router

router = Router()
PAGE = 5


def _line(item: history.HistoryItem) -> str:
    if item.kind == "order":
        head = f"💵 <b>{item.public_id}</b> · {fmt_dt(item.created_at)}"
        body = f"{format_rub(item.amount_rub or 0)} ₽ → {format_usdt(item.usdt)} USDT"
    else:
        net = NETWORK[item.network] if item.network else ""
        head = f"📤 <b>{item.public_id}</b> · {fmt_dt(item.created_at)}"
        body = f"{format_usdt(item.usdt)} USDT ({net})"
        if item.explorer_url:
            body += f' · <a href="{item.explorer_url}">транзакция</a>'
    return f"{head}\n{body}\nСтатус: {item.status_label}"


async def show(ctx: Ctx, page: int, edit: bool) -> None:
    assert ctx.user is not None
    async with ctx.container.db() as session:
        items, nxt = await history.feed(session, ctx.user.id, limit=PAGE, offset=page * PAGE)
    if not items and page == 0:
        await ctx.reply(texts.HISTORY_EMPTY)
        return
    text = texts.HISTORY_TITLE.format(page=page + 1) + "\n\n".join(_line(i) for i in items)
    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append((texts.HISTORY_PREV, f"hist:{page - 1}"))
    if nxt is not None:
        nav.append((texts.HISTORY_NEXT, f"hist:{page + 1}"))
    markup = keyboards.inline(nav) if nav else None
    if edit:
        await ctx.edit(text, markup)
    else:
        await ctx.reply(text, markup)


@router.command("history")
@router.text(texts.BTN_HISTORY)
@active_only
async def cmd_history(ctx: Ctx) -> None:
    await show(ctx, 0, edit=False)


@router.callback("hist:")
@active_only
async def cb_page(ctx: Ctx) -> None:
    try:
        page = max(int(ctx.cb_data.split(":", 1)[1]), 0)
    except ValueError:
        page = 0
    await ctx.answer()
    await show(ctx, page, edit=True)
