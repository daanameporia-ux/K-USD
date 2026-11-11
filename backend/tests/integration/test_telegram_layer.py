from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from app.telegram.dispatcher import ERROR_TEXT, KeyedLock
from app.telegram.fsm import FSM
from app.telegram.router import Ctx, Router


def build_router(log: list[str]) -> Router:
    r = Router()

    @r.command("start")
    async def start(ctx: Ctx) -> None:
        log.append(f"start:{ctx.args}")
        await ctx.reply("hello")

    @r.command("ask")
    async def ask(ctx: Ctx) -> None:
        await ctx.fsm.set("asking", {"n": 1})
        await ctx.reply("question?")

    @r.state("asking")
    async def answer(ctx: Ctx) -> None:
        log.append(f"answer:{ctx.text}:{ctx.fsm.data['n']}")
        await ctx.fsm.clear()

    @r.text("💵 Купить USDT")
    async def buy(ctx: Ctx) -> None:
        log.append("buy")

    @r.callback("ord:")
    async def cb_ord(ctx: Ctx) -> None:
        log.append(f"ord:{ctx.cb_data}")

    @r.callback("ord:confirm:")
    async def cb_confirm(ctx: Ctx) -> None:
        log.append(f"confirm:{ctx.cb_data}")

    @r.command("boom")
    async def boom(ctx: Ctx) -> None:
        raise RuntimeError("boom")

    @r.command("slow")
    async def slow(ctx: Ctx) -> None:
        log.append(f"slow-start:{ctx.args}")
        await asyncio.sleep(0.05)
        log.append(f"slow-end:{ctx.args}")

    @r.fallback()
    async def fallback(ctx: Ctx) -> None:
        log.append(f"fallback:{ctx.text or ctx.cb_data}")

    return r


async def test_routing_commands_texts_callbacks_states(container, fake_tg, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    d = container.dispatcher
    await d.process(updates.message(1, "/start@test_bot payload"))
    await d.process(updates.message(1, "💵 Купить USDT"))
    await d.process(updates.callback(1, "ord:confirm:5"))
    await d.process(updates.callback(1, "ord:cancel:5"))
    await d.process(updates.message(1, "/ask"))
    await d.process(updates.message(1, "42"))
    await d.process(updates.message(1, "random"))
    await d.process(updates.message(1, "/unknown"))
    assert log == [
        "start:payload",
        "buy",
        "confirm:ord:confirm:5",
        "ord:ord:cancel:5",
        "answer:42:1",
        "fallback:random",
        "fallback:/unknown",
    ]
    # every callback query is answered
    assert len(fake_tg.of("answerCallbackQuery")) == 2


async def test_state_survives_restart(container, config, engine, fake_tg, updates) -> None:
    from app.container import Container

    log: list[str] = []
    container.dispatcher.router = build_router(log)
    await container.dispatcher.process(updates.message(7, "/ask"))
    # "restart": brand new container on the same DB
    c2 = Container(config, engine=engine, tg_transport=fake_tg.transport())
    c2.dispatcher.router = build_router(log)
    await c2.dispatcher.process(updates.message(7, "yes"))
    await c2.aclose()
    assert log == ["answer:yes:1"]


async def test_command_resets_state(container, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    await container.dispatcher.process(updates.message(3, "/ask"))
    await container.dispatcher.process(updates.message(3, "/start"))
    await container.dispatcher.process(updates.message(3, "42"))
    assert log == ["start:", "fallback:42"]
    assert (await FSM.load(container.db, 3)).state is None


async def test_duplicate_update_ignored(container, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    upd = updates.message(1, "/start")
    await container.dispatcher.process(upd)
    await container.dispatcher.process(upd)
    assert log == ["start:"]


async def test_handler_exception_isolated(container, fake_tg, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    await container.dispatcher.process(updates.message(1, "/boom"))
    assert fake_tg.last_text(1) == ERROR_TEXT
    await container.dispatcher.process(updates.message(1, "/start"))
    assert log == ["start:"]


async def test_group_messages_ignored(container, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    await container.dispatcher.process(updates.message(1, "/start", chat_type="group", chat_id=-5))
    await container.dispatcher.process({"update_id": 999})
    await container.dispatcher.process({"bad": "update"})
    assert log == []


async def test_same_user_sequential_different_users_parallel(container, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    d = container.dispatcher
    await asyncio.gather(
        d.process(updates.message(1, "/slow a")),
        d.process(updates.message(1, "/slow b")),
    )
    assert log == ["slow-start:a", "slow-end:a", "slow-start:b", "slow-end:b"]
    log.clear()
    await asyncio.gather(
        d.process(updates.message(1, "/slow a")),
        d.process(updates.message(2, "/slow b")),
    )
    assert set(log[:2]) == {"slow-start:a", "slow-start:b"}
    assert len(d.locks) == 0


async def test_keyed_lock_cleanup() -> None:
    lock = KeyedLock()
    async with lock(1):
        assert len(lock) == 1
    assert len(lock) == 0


async def test_webhook_secret_and_processing(container, updates, config) -> None:
    from app.main import create_app

    log: list[str] = []
    container.dispatcher.router = build_router(log)
    app = create_app(config, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        bad = await client.post(
            "/tg/webhook", json=updates.message(1, "/start"), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}
        )
        assert bad.status_code == 403
        missing = await client.post("/tg/webhook", json=updates.message(1, "/start"))
        assert missing.status_code == 403
        ok = await client.post(
            "/tg/webhook",
            json=updates.message(1, "/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": config.telegram_webhook_secret},
        )
        assert ok.status_code == 200
    await container.dispatcher.drain()
    assert log == ["start:"]


async def test_polling_loop(container, fake_tg, updates) -> None:
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    fake_tg.updates_queue = [updates.message(1, "/start"), updates.message(2, "/start")]
    stop = asyncio.Event()

    original = container.tg.get_updates

    async def get_updates(offset, timeout=30):
        result = await original(offset, timeout)
        if not result:
            stop.set()
        return result

    container.tg.get_updates = get_updates
    await asyncio.wait_for(container.dispatcher.run_polling(stop), 5)
    await container.dispatcher.drain()
    assert fake_tg.of("deleteWebhook")
    assert fake_tg.of("getUpdates")[1].params["offset"] == 3
    assert log == ["start:", "start:"]


async def test_bot_blocked_marks_user(container, fake_tg, updates) -> None:
    from sqlalchemy import select

    from app.models import User

    async with container.db.begin() as s:
        s.add(User(tg_id=55, markup_pct=5))
    fake_tg.script("sendMessage", {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"})
    log: list[str] = []
    container.dispatcher.router = build_router(log)
    await container.dispatcher.process(updates.message(55, "/start"))
    async with container.db() as s:
        user = (await s.execute(select(User).where(User.tg_id == 55))).scalar_one()
    assert user.bot_blocked is True


async def test_cleanup_seen(container, updates) -> None:
    await container.dispatcher.process(updates.message(1, "/x"))
    assert await container.dispatcher.cleanup_seen() == 0
    container.clock.advance(days=8)
    assert await container.dispatcher.cleanup_seen() == 1
