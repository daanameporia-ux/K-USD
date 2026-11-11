from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.domain.amounts import MSG_RUB_INTEGER
from app.domain.enums import OrderStatus
from app.jobs.outbox import send_notifications
from app.models import Order, User
from app.services import orders
from app.telegram import texts
from app.telegram.fsm import FSM
from tests.conftest import make_staff, seed_rate


async def all_orders(container) -> list[Order]:
    async with container.db() as s:
        return list((await s.execute(select(Order).order_by(Order.id))).scalars())


async def start_buy(bot, tg_id=30, amount="1000"):
    await bot.register(tg_id)
    await bot.send(tg_id, texts.BTN_BUY)
    await bot.send(tg_id, amount)


async def test_full_purchase_flow(bot, container, fake_tg) -> None:
    await seed_rate(container, "100")
    await bot.register(30)
    await bot.send(30, texts.BTN_BUY)
    assert bot.last(30) == "Введите сумму в рублях, от 1 000 до 100 000. Только целое число, без копеек."
    await bot.send(30, "1000.50")
    assert bot.last(30) == MSG_RUB_INTEGER
    assert (await FSM.load(container.db, 30)).state == "buy:amount"
    await bot.send(30, "500")
    assert "от 1 000 до 100 000" in bot.last(30)
    await bot.send(30, "1 000")
    preview = bot.last(30)
    assert "Сумма к оплате: <b>1 000 ₽</b>" in preview
    assert "Комиссия сервиса: 5% (50 ₽)" in preview and "К зачислению: <b>9.50 USDT</b>" in preview
    assert bot.callback_data(30) == ["buy:confirm:1000:9.50", "nav:cancel"]
    preview_msg_id = fake_tg.of("sendMessage")[-1]
    msg_id = fake_tg.next_message_id

    await bot.click(30, "buy:confirm:1000:9.50", message_id=msg_id)
    photo = fake_tg.of("sendPhoto")[-1]
    assert photo.params["photo"].startswith(b"\x89PNG")
    assert "Будет зачислено: <b>9.50 USDT</b>" in photo.params["caption"]
    buttons = [b for row in photo.params["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons[0]["text"] == texts.BUY_PAY and buttons[0]["url"].startswith("https://qr.nspk.ru/MOCK")
    assert buttons[1]["callback_data"].startswith("ord:cancel:")
    assert preview_msg_id

    # double press on the same preview -> still one order
    await bot.click(30, "buy:confirm:1000:9.50", message_id=msg_id)
    assert fake_tg.of("answerCallbackQuery")[-1].params["text"] == texts.BUY_ALREADY_CREATED
    (order,) = await all_orders(container)
    assert order.status == OrderStatus.pending and order.tg_message_id == fake_tg.next_message_id

    staff = await make_staff(container)
    await orders.simulate_payment(container, order.id, amount_rub=None, staff_id=staff.id)
    await send_notifications(container)
    assert "Оплата по заявке" in bot.last(30) and "Зачислено: <b>9.50 USDT</b>" in bot.last(30)
    edit = fake_tg.of("editMessageCaption")[-1]
    assert edit.params["message_id"] == order.tg_message_id and edit.params["reply_markup"] == {"inline_keyboard": []}
    await bot.send(30, "/balance")
    assert "Доступно: <b>9.50 USDT</b>" in bot.last(30)


async def test_price_changed_requires_reconfirm(bot, container, fake_tg) -> None:
    await seed_rate(container, "100")
    await start_buy(bot, 31)
    container.clock.advance(seconds=1)
    await seed_rate(container, "90")
    await bot.click(31, "buy:confirm:1000:9.50", message_id=500)
    assert bot.last(31).startswith(texts.BUY_PRICE_CHANGED)
    assert bot.callback_data(31) == ["buy:confirm:1000:10.55", "nav:cancel"]
    assert await all_orders(container) == []
    await bot.click(31, "buy:confirm:1000:10.55", message_id=500)
    (order,) = await all_orders(container)
    assert order.usdt_amount == Decimal("10.55")


async def test_active_order_shown_and_cancel(bot, container, fake_tg) -> None:
    await seed_rate(container, "100")
    await start_buy(bot, 32)
    await bot.click(32, "buy:confirm:1000:9.50", message_id=600)
    (order,) = await all_orders(container)
    photos = len(fake_tg.of("sendPhoto"))
    await bot.send(32, "/buy")
    assert len(fake_tg.of("sendPhoto")) == photos + 1
    assert texts.BUY_ACTIVE_ORDER in fake_tg.texts_to(32)

    # foreign user cannot cancel it
    await bot.register(33)
    await bot.click(33, f"ord:cancel:{order.id}")
    answer = fake_tg.of("answerCallbackQuery")[-1].params
    assert answer["text"] == "Заявка не найдена" and answer["show_alert"] is True
    assert (await all_orders(container))[0].status == OrderStatus.pending

    await bot.click(32, f"ord:cancel:{order.id}")
    assert bot.last(32) == f"Заявка <b>{order.public_id}</b> отменена."
    assert (await all_orders(container))[0].status == OrderStatus.cancelled
    await bot.click(32, "ord:cancel:abc")


async def test_no_rate_and_blocked_orders(bot, container) -> None:
    await bot.register(34)
    await bot.send(34, texts.BTN_BUY)
    assert bot.last(34) == "Покупка временно недоступна, попробуйте через несколько минут."
    await seed_rate(container, "100")
    async with container.db.begin() as s:
        user = (await s.execute(select(User).where(User.tg_id == 34))).scalar_one()
        user.can_create_orders = False
    await bot.send(34, "/buy")
    assert bot.last(34) == "Создание заявок приостановлено. Обратитесь в поддержку."


async def test_rate_disappears_while_entering_amount(bot, container) -> None:
    await seed_rate(container, "100")
    await bot.register(35)
    await bot.send(35, texts.BTN_BUY)
    container.clock.advance(seconds=200)
    await bot.send(35, "1000")
    assert bot.last(35) == "Покупка временно недоступна, попробуйте через несколько минут."
    assert (await FSM.load(container.db, 35)).state is None


async def test_provider_failure_message(bot, container) -> None:
    await seed_rate(container, "100")
    await start_buy(bot, 36)
    container.payments.get("mock").fail_next_create = RuntimeError("down")
    await bot.click(36, "buy:confirm:1000:9.50", message_id=700)
    assert bot.last(36) == orders.MSG_PROVIDER_FAILED


async def test_cancel_in_the_middle(bot, container) -> None:
    await seed_rate(container, "100")
    await bot.register(37)
    await bot.send(37, texts.BTN_BUY)
    await bot.send(37, "/cancel")
    await bot.send(37, "1000")
    assert bot.last(37) == texts.UNKNOWN
    await bot.click(37, "buy:confirm:bad")


async def test_restart_between_steps(bot, container, config, engine, fake_tg, updates) -> None:
    from app.container import Container
    from tests.conftest import BotDriver

    await seed_rate(container, "100")
    await bot.register(38)
    await bot.send(38, texts.BTN_BUY)
    c2 = Container(config, engine=engine, tg_transport=fake_tg.transport(), clock=container.clock)
    try:
        bot2 = BotDriver(c2, fake_tg, updates)
        await bot2.send(38, "2000")
        assert "К зачислению: <b>19.00 USDT</b>" in bot2.last(38)
    finally:
        await c2.aclose()
