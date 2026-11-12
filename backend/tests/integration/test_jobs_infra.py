from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.jobs.outbox import send_notifications
from app.jobs.scheduler import Job, Scheduler
from app.models import NotificationOutbox, Staff
from app.services import notify


async def test_scheduler_run_once_and_failure_alert(container, clock) -> None:
    calls: list[str] = []

    async def ok(c):
        calls.append("ok")

    async def bad(c):
        raise RuntimeError("kaput")

    sched = Scheduler(container, [Job("ok", ok, 1), Job("bad", bad, 1)])
    assert await sched.run_once("ok")
    assert sched.state["ok"].last_success == clock.now()
    assert not await sched.run_once("bad")
    assert not await sched.run_once("bad")
    assert "kaput" in sched.state["bad"].last_error
    async with container.db() as s:
        rows = (await s.execute(select(NotificationOutbox))).scalars().all()
    assert len(rows) == 1 and "bad" in rows[0].payload["params"]["text"]


async def test_scheduler_advisory_lock(container) -> None:
    async def ok(c):
        pass

    job = Job("locked", ok, 1)
    sched = Scheduler(container, [job])
    async with container.engine.connect() as conn:
        assert await conn.scalar(select(func.pg_try_advisory_lock(job.lock_key)))
        assert not await sched.run_once(job)
        await conn.scalar(select(func.pg_advisory_unlock(job.lock_key)))
    assert sched.state["locked"].skipped == 1
    assert await sched.run_once(job)


async def test_scheduler_loop_start_stop(container) -> None:
    calls: list[int] = []

    async def tick(c):
        calls.append(1)

    async def interval(c):
        return 0.01

    sched = Scheduler(container, [Job("tick", tick, interval)])
    sched.start()
    assert sched.running
    await asyncio.sleep(0.1)
    await sched.stop()
    assert len(calls) >= 2
    assert not sched.running


async def test_outbox_delivery_retry_and_failure(container, fake_tg, clock) -> None:
    async with container.db.begin() as s:
        ok = await notify.message(s, 5, "hello", now=clock.now())
        blocked = await notify.message(s, 6, "blocked", now=clock.now())
        flaky = await notify.message(s, 7, "flaky", now=clock.now())
        later = await notify.remove_buttons(s, 5, 99, now=clock.now())
        alert = await notify.alert(s, "alert!", now=clock.now())
    fake_tg.script(
        "sendMessage",
        {"ok": True, "result": {}},
        {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
        *[{"ok": False, "error_code": 500, "description": "Internal"}] * 4,
    )
    assert await send_notifications(container) == 3  # ok + editMessageReplyMarkup + alert to STAFF_CHAT_ID
    async with container.db() as s:
        rows = {r.id: r for r in (await s.execute(select(NotificationOutbox))).scalars()}
    assert rows[ok.id].sent_at is not None
    assert rows[blocked.id].failed is True
    assert rows[flaky.id].sent_at is None and rows[flaky.id].attempts == 1 and not rows[flaky.id].failed
    assert rows[alert.id].sent_at is not None
    staff_calls = [c for c in fake_tg.of("sendMessage") if c.params["chat_id"] == -100500]
    assert staff_calls and staff_calls[0].params["text"] == "alert!"
    edit = fake_tg.of("editMessageReplyMarkup")[0]
    assert edit.params["message_id"] == 99 and later is None
    # not due yet
    assert await send_notifications(container) == 0
    clock.advance(seconds=11)
    assert await send_notifications(container) == 1


async def test_outbox_staff_fallback_to_admins(container, fake_tg, clock, monkeypatch) -> None:
    monkeypatch.setattr(container.config, "staff_chat_id", None)
    async with container.db.begin() as s:
        s.add_all(
            [
                Staff(role="admin", tg_id=501),
                Staff(role="operator", tg_id=502),
                Staff(role="admin", tg_id=503, is_active=False),
            ]
        )
        await notify.alert(s, "x", now=clock.now())
    await send_notifications(container)
    assert [c.params["chat_id"] for c in fake_tg.of("sendMessage")] == [501]


async def test_outbox_gives_up_after_max_attempts(container, fake_tg, clock) -> None:
    async with container.db.begin() as s:
        row = await notify.message(s, 8, "x", now=clock.now())
    for _ in range(10):
        fake_tg.script("sendMessage", *[{"ok": False, "error_code": 502, "description": "Bad Gateway"}] * 4)
        await send_notifications(container)
        clock.advance(hours=1)
    async with container.db() as s:
        fresh = await s.get(NotificationOutbox, row.id)
    assert fresh.failed and fresh.attempts == 10
