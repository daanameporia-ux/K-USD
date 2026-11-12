from __future__ import annotations

from decimal import Decimal

from app.services import ledger
from app.services import users as users_svc
from app.telegram import texts
from tests.integration.test_ledger import make_order


async def test_balance_screen(bot, container, clock) -> None:
    await bot.register(20)
    await bot.send(20, texts.BTN_BALANCE)
    assert "Доступно: <b>0.00 USDT</b>" in bot.last(20)
    async with container.db.begin() as s:
        user = await users_svc.get_by_tg(s, 20)
        user = await users_svc.lock(s, user.id)
        order = await make_order(s, user)
        await ledger.deposit(s, user, Decimal("9.5"), order_id=order.id, now=clock.now())
        await ledger.hold(s, user, Decimal("2"), withdrawal_id=None, now=clock.now())
    await bot.send(20, "/balance")
    text = bot.last(20)
    assert "Доступно: <b>7.50 USDT</b>" in text
    assert "В обработке (вывод): 2.00 USDT" in text
    assert "Всего куплено: 9.50 USDT" in text


async def test_balance_requires_registration(bot) -> None:
    await bot.send(21, "/start")
    await bot.send(21, "/balance")
    assert bot.last(21) == texts.REGISTRATION_REQUIRED
