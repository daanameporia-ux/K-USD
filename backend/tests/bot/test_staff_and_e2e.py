from __future__ import annotations

from decimal import Decimal

import respx
from sqlalchemy import select

from app.domain.enums import StaffRole, WithdrawalStatus
from app.jobs.outbox import send_notifications
from app.models import Order, Staff, Withdrawal
from app.services import orders, withdrawals
from app.services import settings as settings_svc
from app.services import staff as staff_svc
from app.services.actor import Actor
from app.telegram import texts
from tests.chain_fixtures import TRON_TO, TRON_TX, mock_tron
from tests.conftest import make_staff, seed_rate


async def test_admin_command(bot, container) -> None:
    await bot.register(60)
    await bot.send(60, "/admin")
    assert bot.last(60) == texts.UNKNOWN
    await make_staff(container, tg_id=60, role="operator")
    await bot.send(60, "/admin")
    assert bot.last(60) == texts.ADMIN_OPEN
    assert bot.buttons(60)[0]["web_app"]["url"] == "https://example.test/admin/"


async def test_invite_bound_on_start(bot, container, fake_tg) -> None:
    sup = await make_staff(container, tg_id=61, role="admin", is_superadmin=True)
    result = await staff_svc.add(
        container, Actor(sup.id, StaffRole.admin, True, 61), role="operator", username="@Invitee"
    )
    assert result.invited
    await bot.send(62, "/start", username="invitee")
    async with container.db() as s:
        staff = (await s.execute(select(Staff).where(Staff.tg_id == 62))).scalar_one()
    assert staff.id == result.staff.id and staff.invite_expires_at is None
    await send_notifications(container)
    assert "принял приглашение" in bot.last(61)
    assert any(c.method == "setMyCommands" and c.params.get("scope", {}).get("chat_id") == 62 for c in fake_tg.calls)
    # nickname change later does not matter: tg_id is authoritative
    await bot.send(62, "/admin", username="renamed")
    assert bot.last(62) == texts.ADMIN_OPEN
    await bot.send(63, "/start", username="invitee")
    await bot.send(63, "/admin", username="invitee")
    assert bot.last(63) != texts.ADMIN_OPEN


async def test_full_scenario_bot(bot, container, fake_tg) -> None:
    """/start → registration → purchase → simulated payment → balance → withdrawal → operator hash → notification."""
    await seed_rate(container, "100")
    async with container.db.begin() as s:
        await settings_svc.update(s, {"withdraw_min_usdt": "1"}, staff_id=None, now=container.clock.now())
    await bot.register(64)
    await bot.send(64, texts.BTN_BUY)
    await bot.send(64, "1400")
    assert "К зачислению: <b>13.30 USDT</b>" in bot.last(64)
    await bot.click(64, "buy:confirm:1400:13.30", message_id=9001)
    async with container.db() as s:
        order = (await s.execute(select(Order))).scalar_one()
    admin = await make_staff(container, tg_id=65, role="admin")
    await orders.simulate_payment(container, order.id, amount_rub=None, staff_id=admin.id)
    await send_notifications(container)
    assert "Зачислено: <b>13.30 USDT</b>" in bot.last(64)
    await bot.send(64, texts.BTN_BALANCE)
    assert "Доступно: <b>13.30 USDT</b>" in bot.last(64)

    await bot.send(64, texts.BTN_WITHDRAW)
    await bot.click(64, "wd:net:TRC20")
    await bot.send(64, TRON_TO)
    await bot.send(64, "12.67")
    await bot.click(64, "wd:confirm", message_id=9002)
    async with container.db() as s:
        w = (await s.execute(select(Withdrawal))).scalar_one()
    assert w.amount_to_send == Decimal("8.67")

    operator = await make_staff(container, tg_id=66, role="operator")
    actor = Actor(operator.id, StaffRole.operator)
    await withdrawals.take(container, w.id, actor)
    with respx.mock:
        mock_tron(respx.mock, "ok")
        result = await withdrawals.submit_hash(container, w.id, actor, TRON_TX)
    assert result.withdrawal.status == WithdrawalStatus.completed
    await send_notifications(container)
    final = bot.last(64)
    assert "исполнен" in final and TRON_TX in final and "tronscan.org" in final
    await bot.send(64, "/balance")
    assert "Доступно: <b>0.63 USDT</b>" in bot.last(64) and "Всего выведено: 12.67 USDT" in bot.last(64)
