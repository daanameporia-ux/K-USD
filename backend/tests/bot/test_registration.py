from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.domain.enums import KycStatus, UserStatus
from app.models import User
from app.services import settings as settings_svc
from app.telegram import texts
from app.telegram.dispatcher import ERROR_TEXT
from app.telegram.setup import configure_bot


async def get_user(container, tg_id: int) -> User:
    async with container.db() as s:
        return (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one()


async def test_start_registration_flow(bot, container, clock) -> None:
    await bot.send(10, "/start", username="alice")
    user = await get_user(container, 10)
    assert user.status == UserStatus.registering
    assert user.tg_username == "alice"
    assert bot.last(10) == texts.WELCOME_NEW
    assert bot.callback_data(10) == ["reg:start"]

    async with container.db.begin() as s:
        await settings_svc.update(s, {"default_markup_pct": "4.5"}, staff_id=None, now=clock.now())

    await bot.click(10, "reg:start")
    user = await get_user(container, 10)
    assert user.status == UserStatus.active
    assert user.kyc_status == KycStatus.passed
    assert user.markup_pct == Decimal("4.50")
    assert user.registered_at == clock.now()
    assert bot.last(10) == texts.REGISTRATION_DONE
    labels = [b["text"] for b in bot.buttons(10)]
    assert labels == [
        texts.BTN_BUY,
        texts.BTN_BALANCE,
        texts.BTN_WITHDRAW,
        texts.BTN_HISTORY,
        texts.BTN_SUPPORT,
        texts.BTN_APP,
    ]
    assert bot.buttons(10)[-1]["web_app"]["url"] == "https://example.test/app/"

    await bot.send(10, "/start")
    assert bot.last(10) == texts.MAIN_MENU
    # repeated registration click is harmless
    await bot.click(10, "reg:start")
    assert (await get_user(container, 10)).registered_at == clock.now()


async def test_unregistered_user_gated(bot) -> None:
    await bot.send(11, "/start")
    await bot.send(11, "/app")
    assert bot.last(11) == texts.REGISTRATION_REQUIRED
    await bot.send(11, "hello")
    assert bot.last(11) == texts.REGISTRATION_REQUIRED
    await bot.send(11, "/help")
    assert "Как это работает" in bot.last(11)


async def test_help_cancel_support_app(bot, container) -> None:
    await bot.register(12)
    await bot.send(12, "/help")
    assert "@support" in bot.last(12)
    await bot.send(12, texts.BTN_SUPPORT)
    assert bot.last(12).startswith("🆘 Поддержка: @support")
    await bot.send(12, "/app")
    assert bot.buttons(12)[0]["web_app"]["url"].endswith("/app/")
    from app.telegram.fsm import FSM

    fsm = await FSM.load(container.db, 12)
    await fsm.set("some:state", {"x": 1})
    await bot.send(12, "/cancel")
    assert bot.last(12) == texts.CANCELLED
    assert (await FSM.load(container.db, 12)).state is None
    await bot.click(12, "nav:cancel")
    await bot.send(12, "what?")
    assert bot.last(12) == texts.UNKNOWN
    await bot.click(12, "unknown:cb")


async def test_blocked_user(bot, container) -> None:
    await bot.register(13)
    async with container.db.begin() as s:
        user = (await s.execute(select(User).where(User.tg_id == 13))).scalar_one()
        user.status = UserStatus.blocked
    await bot.send(13, "/start")
    assert bot.last(13) == texts.BLOCKED
    await bot.click(13, "reg:start")
    assert (await get_user(container, 13)).status == UserStatus.blocked


async def test_chat_member_updates_bot_blocked(bot, container, updates) -> None:
    await bot.register(14)
    await container.dispatcher.process(updates.chat_member(14, "kicked"))
    assert (await get_user(container, 14)).bot_blocked is True
    await container.dispatcher.process(updates.chat_member(14, "member"))
    assert (await get_user(container, 14)).bot_blocked is False


async def test_handler_error_message(bot, container, monkeypatch) -> None:
    from app.services import users as users_svc

    async def broken(*a, **kw):
        raise RuntimeError("db down")

    await bot.send(15, "/start")
    monkeypatch.setattr(users_svc, "start_registration", broken)
    await bot.click(15, "reg:start")
    assert bot.last(15) == ERROR_TEXT


async def test_configure_bot(container, fake_tg) -> None:
    assert await configure_bot(container, staff_tg_ids=[1000])
    methods = [c.method for c in fake_tg.calls]
    assert methods == ["setWebhook", "setMyCommands", "setChatMenuButton", "setMyCommands"]
    assert fake_tg.calls[0].params["url"] == "https://example.test/tg/webhook"
    assert fake_tg.calls[0].params["secret_token"] == container.config.telegram_webhook_secret
    assert {"command": "admin", "description": "Админка"} in fake_tg.calls[3].params["commands"]
    fake_tg.script("setWebhook", {"ok": False, "error_code": 401, "description": "Unauthorized"})
    assert not await configure_bot(container)
