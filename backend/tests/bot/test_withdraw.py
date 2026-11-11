from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.domain.enums import WithdrawalStatus
from app.models import User, Withdrawal
from app.services import users as users_svc
from app.telegram import texts
from app.telegram.fsm import FSM
from tests.chain_fixtures import BSC_TO, TRON_TO
from tests.conftest import fund, reload


async def registered_with_balance(bot, container, tg_id, amount="120.555"):
    await bot.register(tg_id)
    async with container.db() as s:
        user = await users_svc.get_by_tg(s, tg_id)
    await fund(container, user, amount)
    return user


async def test_full_withdraw_flow(bot, container, fake_tg) -> None:
    user = await registered_with_balance(bot, container, 40)
    await bot.send(40, texts.BTN_WITHDRAW)
    assert "Доступно к выводу: <b>120.555 USDT</b>" in bot.last(40)
    assert bot.callback_data(40) == ["wd:net:TRC20", "wd:net:BEP20", "nav:cancel"]
    assert [b["text"] for b in bot.buttons(40)][:2] == ["TRC-20 (комиссия 4.00 USDT)", "BEP-20 (комиссия 2.00 USDT)"]

    await bot.click(40, "wd:net:BEP20")
    assert "сети <b>BEP-20</b>" in bot.last(40)
    await bot.send(40, "0x123")
    assert "40 шестнадцатеричных" in bot.last(40)
    await bot.send(40, "0x55d398326f99059ff775485246999027b3197955")
    assert "контракта USDT" in bot.last(40)
    await bot.send(40, BSC_TO.lower())
    assert "Введите сумму списания" in bot.last(40)
    await bot.send(40, "10,555")
    assert "не более 2 знаков" in bot.last(40)
    await bot.send(40, "500")
    assert "Недостаточно средств" in bot.last(40)
    assert (await FSM.load(container.db, 40)).state == "wd:amount"
    await bot.click(40, "wd:all")
    preview = bot.last(40)
    assert f"<code>{BSC_TO}</code>" in preview
    assert "Списывается с баланса: 120.55 USDT" in preview and "<b>К получению: 118.55 USDT</b>" in preview
    assert "Перевод в другую сеть" in preview

    await bot.click(40, "wd:confirm", message_id=777)
    assert "Заявка на вывод <b>W-" in bot.last(40)
    await bot.click(40, "wd:confirm", message_id=777)
    assert fake_tg.of("answerCallbackQuery")[-1].params["text"] == texts.WD_ALREADY
    async with container.db() as s:
        (w,) = (await s.execute(select(Withdrawal))).scalars().all()
    assert w.amount == Decimal("120.55") and w.status == WithdrawalStatus.pending
    u = await reload(container, User, user.id)
    assert (u.balance, u.hold) == (Decimal("0.005"), Decimal("120.55"))

    await bot.click(40, f"wd:cancel:{w.id}")
    assert "отменён" in bot.last(40)
    u = await reload(container, User, user.id)
    assert (u.balance, u.hold) == (Decimal("120.555"), Decimal(0))


async def test_withdraw_typed_amount_trc20(bot, container) -> None:
    await registered_with_balance(bot, container, 41)
    await bot.send(41, "/withdraw")
    await bot.click(41, "wd:net:TRC20")
    await bot.send(41, TRON_TO)
    await bot.send(41, "60")
    assert "<b>К получению: 56.00 USDT</b>" in bot.last(41)
    await bot.send(41, "/cancel")
    await bot.click(41, "wd:confirm", message_id=1)
    await bot.click(41, "wd:all")
    await bot.click(41, "wd:net:XYZ")
    await bot.click(41, "wd:cancel:x")
    async with container.db() as s:
        assert (await s.execute(select(Withdrawal))).scalars().all() == []


async def test_withdraw_not_enough_balance(bot, container) -> None:
    await registered_with_balance(bot, container, 42, "10")
    await bot.send(42, texts.BTN_WITHDRAW)
    assert "Минимальная сумма вывода — 50.00 USDT" in bot.last(42)


async def test_history_pages(bot, container) -> None:
    user = await registered_with_balance(bot, container, 43, "1000")
    await bot.send(43, texts.BTN_HISTORY)
    assert "B-9" in bot.last(43)
    from app.domain.enums import Network
    from app.services import withdrawals

    for i in range(6):
        container.clock.advance(seconds=1)
        await withdrawals.create(container, user.id, Network.TRC20, TRON_TO, Decimal(50 + i), idempotency_key=str(i))
    await bot.send(43, "/history")
    first = bot.last(43)
    assert "стр. 1" in first and first.count("📤") == 5
    assert bot.callback_data(43) == ["hist:1"]
    await bot.click(43, "hist:1")
    second = bot.last(43)
    assert "стр. 2" in second and second.count("📤") == 1 and second.count("💵") == 1
    assert bot.callback_data(43) == ["hist:0"]


async def test_history_empty(bot) -> None:
    await bot.register(44)
    await bot.send(44, "/history")
    assert bot.last(44) == texts.HISTORY_EMPTY
