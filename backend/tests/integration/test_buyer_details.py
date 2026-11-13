"""Buyer details with every order: SBP phone, full name, payment screenshot; per-client switches."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.domain.enums import OrderStatus
from app.jobs.outbox import send_notifications
from app.models import Order, OrderProof, User
from app.services import orders
from app.telegram import texts
from app.telegram.fsm import FSM
from tests.conftest import login_client, login_staff, make_staff, make_user, pay, seed_rate

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
BUYER = {"buyer_phone": "8 (916) 123-45-67", "buyer_name": " Иванов  Иван Иванович "}

pytestmark = pytest.mark.buyer_details


def idem() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def create(api, h, **extra) -> httpx.Response:
    return await api.post("/api/client/orders", headers={**h, **idem()}, json={"amount_rub": "1000", **extra})


async def paid_order(api, container, h, **extra) -> dict:
    resp = await create(api, h, **(extra or BUYER))
    assert resp.status_code == 200, resp.text
    async with container.db() as s:
        order = await s.get(Order, resp.json()["id"])
    await pay(container, order)
    return (await api.get(f"/api/client/orders/{order.id}", headers=h)).json()


async def test_new_clients_have_all_requirements_on(api, container, clock) -> None:
    h = await login_client(api, clock, 800)
    me = (await api.get("/api/client/me", headers=h)).json()
    assert me["requirements"] == {"buyer_phone": True, "buyer_name": True, "payment_proof": True}
    assert me["proof_pending_order"] is None


async def test_order_needs_valid_phone_and_name(api, container, clock) -> None:
    await seed_rate(container, "100")
    h = await login_client(api, clock, 801)
    for body, code in [
        ({}, "invalid_buyer_phone"),
        ({"buyer_phone": "+74951234567", "buyer_name": "Иванов Иван Иванович"}, "invalid_buyer_phone"),
        ({"buyer_phone": "+380501234567", "buyer_name": "Иванов Иван Иванович"}, "invalid_buyer_phone"),
        ({"buyer_phone": "+79161234567"}, "invalid_buyer_name"),
        ({"buyer_phone": "+79161234567", "buyer_name": "Иванов"}, "invalid_buyer_name"),
    ]:
        resp = await create(api, h, **body)
        assert resp.status_code == 400 and resp.json()["error"]["code"] == code, resp.text
    async with container.db() as s:
        assert (await s.execute(select(Order))).scalars().all() == []

    check = await api.post("/api/client/orders/buyer-check", headers=h, json={"buyer_phone": "123"})
    assert check.status_code == 400 and check.json()["error"]["code"] == "invalid_buyer_phone"
    check = await api.post("/api/client/orders/buyer-check", headers=h, json=BUYER)
    assert check.json() == {"buyer_phone": "+79161234567", "buyer_name": "Иванов Иван Иванович"}

    resp = await create(api, h, **BUYER)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "pending" and data["proof_required"] is True and data["proof_uploaded_at"] is None
    assert data["buyer_phone"] == "+79161234567" and data["buyer_name"] == "Иванов Иван Иванович"
    # the payment provider gets the same details together with the order
    last = container.payments.get("mock").last_create
    assert last["buyer_phone"] == "+79161234567" and last["buyer_name"] == "Иванов Иван Иванович"


async def test_screenshot_after_payment_gates_the_next_order(api, container, clock) -> None:
    await seed_rate(container, "100")
    h = await login_client(api, clock, 802)
    resp = await create(api, h, **BUYER)
    order_id = resp.json()["id"]
    url = f"/api/client/orders/{order_id}/proof"

    early = await api.post(url, headers=h, content=PNG)  # not paid yet
    assert early.status_code == 409 and early.json()["error"]["code"] == "proof_not_expected"

    async with container.db() as s:
        order = await s.get(Order, order_id)
    await pay(container, order)
    await send_notifications(container)
    me = (await api.get("/api/client/me", headers=h)).json()
    assert me["proof_pending_order"]["id"] == order_id

    blocked = await create(api, h, **BUYER)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "proof_required"
    assert blocked.json()["error"]["details"]["order_id"] == order_id
    preview = await api.post("/api/client/orders/preview", headers=h, json={"amount_rub": "1000"})
    assert preview.status_code == 409 and preview.json()["error"]["code"] == "proof_required"

    bad = await api.post(url, headers=h, content=b"<html>not an image</html>")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_proof"
    huge = await api.post(url, headers=h, content=PNG + b"\x00" * (10 * 1024 * 1024))
    assert huge.status_code == 413

    other = await login_client(api, clock, 803)
    foreign = await api.post(url, headers=other, content=PNG)
    assert foreign.status_code == 404

    ok = await api.post(url, headers=h, content=PNG)
    assert ok.status_code == 200 and ok.json()["proof_uploaded_at"] is not None
    again = await api.post(url, headers=h, content=JPEG)  # a better screenshot replaces the first one
    assert again.status_code == 200
    async with container.db() as s:
        (proof,) = (await s.execute(select(OrderProof))).scalars().all()
    assert (proof.content, proof.content_type, proof.filename, proof.source) == (
        JPEG,
        "image/jpeg",
        f"{order.public_id}.jpg",
        "webapp",
    )

    me = (await api.get("/api/client/me", headers=h)).json()
    assert me["proof_pending_order"] is None
    assert (await create(api, h, **BUYER)).status_code == 200


async def test_admin_sees_details_and_switches_requirements_per_client(api, container, clock) -> None:
    await seed_rate(container, "100")
    h = await login_client(api, clock, 804)
    order = await paid_order(api, container, h)
    await api.post(f"/api/client/orders/{order['id']}/proof", headers=h, content=PNG)
    admin = await login_staff(api, container, clock, 9000)
    operator = await login_staff(api, container, clock, 9001, role="operator")

    card = (await api.get(f"/api/admin/orders/{order['id']}", headers=operator)).json()
    assert card["buyer_phone"] == "+79161234567" and card["buyer_name"] == "Иванов Иван Иванович"
    assert card["proof"] == {
        "content_type": "image/png",
        "filename": f"{order['public_id']}.png",
        "size": len(PNG),
        "source": "webapp",
    }
    file = await api.get(f"/api/admin/orders/{order['id']}/proof", headers=operator)
    assert file.status_code == 200 and file.content == PNG and file.headers["content-type"] == "image/png"
    assert "attachment" in file.headers["content-disposition"]
    assert (await api.get(f"/api/admin/orders/{order['id']}/proof", headers=h)).status_code == 403
    assert (await api.get(f"/api/admin/orders/{order['id']}/proof")).status_code == 401

    user_id = card["user_id"]
    off = {"require_buyer_phone": False, "require_buyer_name": False, "require_payment_proof": False}
    denied = await api.patch(f"/api/admin/users/{user_id}", headers=operator, json=off)
    assert denied.status_code == 403
    patched = await api.patch(f"/api/admin/users/{user_id}", headers=admin, json=off)
    assert patched.status_code == 200 and all(patched.json()[k] is False for k in off)
    audit = (await api.get("/api/admin/audit", headers=admin)).json()["items"][0]
    assert audit["action"] == "user.update" and audit["after"] == off

    me = (await api.get("/api/client/me", headers=h)).json()
    assert me["requirements"] == {"buyer_phone": False, "buyer_name": False, "payment_proof": False}
    plain = await paid_order(api, container, h, buyer_phone="мусор", buyer_name="x")  # ignored when switched off
    assert plain["buyer_phone"] is None and plain["buyer_name"] is None and plain["proof_required"] is False
    assert (await create(api, h)).status_code == 200  # no screenshot needed any more

    # each switch works on its own
    other = await login_client(api, clock, 805)
    async with container.db() as s:
        other_id = (await s.execute(select(User.id).where(User.tg_id == 805))).scalar_one()
    await api.patch(f"/api/admin/users/{other_id}", headers=admin, json={"require_buyer_name": False})
    only_phone = await create(api, other, buyer_phone="+79261112233")
    assert only_phone.status_code == 200 and only_phone.json()["buyer_name"] is None
    assert only_phone.json()["buyer_phone"] == "+79261112233" and only_phone.json()["proof_required"] is True


async def test_switching_proof_off_unblocks_orders_that_waited_for_it(container) -> None:
    await seed_rate(container, "100")
    user = await make_user(container, 806)
    kw = {"buyer_phone": "+79161234567", "buyer_name": "Иванов Иван"}
    first = await orders.create(container, user.id, 1000, source="bot", idempotency_key="a", **kw)
    await pay(container, first.order)
    with pytest.raises(orders.ProofRequired):
        await orders.create(container, user.id, 1000, source="bot", idempotency_key="b", **kw)
    async with container.db.begin() as s:
        (await s.get(User, user.id)).require_payment_proof = False
    second = await orders.create(container, user.id, 1000, source="bot", idempotency_key="b", **kw)
    assert second.order.status == OrderStatus.pending and second.order.proof_required is False


# --- bot ---------------------------------------------------------------------------------


def media_update(updates, tg_id: int, *, photo: bool = True, size: int = 100) -> dict:
    update = updates.message(tg_id, "")
    del update["message"]["text"]
    if photo:
        update["message"]["photo"] = [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": size}]
    else:
        update["message"]["document"] = {"file_id": "doc", "file_name": "история.pdf", "file_size": size}
    return update


async def test_bot_asks_phone_and_name_then_screenshot(bot, container, fake_tg, updates) -> None:
    await seed_rate(container, "100")
    await bot.register(40)
    await bot.send(40, texts.BTN_BUY)
    await bot.send(40, "1000")
    assert bot.last(40) == texts.BUY_ASK_PHONE and "СБП" in texts.BUY_ASK_PHONE
    await bot.send(40, "+7 495 123-45-67")
    assert "российском формате" in bot.last(40)
    assert (await FSM.load(container.db, 40)).state == "buy:phone"  # stays on the step until the number is valid
    await bot.send(40, "8 916 123-45-67")
    assert bot.last(40) == texts.BUY_ASK_NAME
    await bot.send(40, "Иванов")
    assert "фамилия, имя и отчество" in bot.last(40)
    assert (await FSM.load(container.db, 40)).state == "buy:name"
    await bot.send(40, "Иванов Иван Иванович")
    preview = bot.last(40)
    assert "К зачислению: <b>9.50 USDT</b>" in preview
    assert "Телефон покупателя (СБП): <b>+79161234567</b>" in preview
    assert "ФИО покупателя: <b>Иванов Иван Иванович</b>" in preview

    await bot.click(40, "buy:confirm:1000:9.50", message_id=fake_tg.next_message_id)
    async with container.db() as s:
        (order,) = (await s.execute(select(Order))).scalars().all()
    assert order.status == OrderStatus.pending and order.proof_required is True
    assert (order.buyer_phone, order.buyer_name) == ("+79161234567", "Иванов Иван Иванович")
    assert (await FSM.load(container.db, 40)).state is None

    # a screenshot before the payment is not expected
    await container.dispatcher.process(media_update(updates, 40))
    assert bot.last(40) == texts.PROOF_NOT_EXPECTED

    staff = await make_staff(container)
    await orders.simulate_payment(container, order.id, amount_rub=None, staff_id=staff.id)
    await send_notifications(container)
    assert "скриншот из истории операций" in bot.last(40) and "все прошлые операции" in bot.last(40)

    await bot.send(40, texts.BTN_BUY)
    assert order.public_id in bot.last(40) and "скриншот" in bot.last(40)
    assert (await FSM.load(container.db, 40)).state is None

    fake_tg.script("getFile", {"ok": True, "result": {"file_id": "big", "file_path": "proof", "file_size": len(PNG)}})
    fake_tg.script("proof", httpx.Response(200, content=PNG))
    await container.dispatcher.process(media_update(updates, 40))
    assert "Скриншот по заявке" in bot.last(40)
    assert fake_tg.of("getFile")[-1].params == {"file_id": "big"}  # the largest photo size
    async with container.db() as s:
        proof = await s.get(OrderProof, order.id)
    assert (proof.content, proof.content_type, proof.source) == (PNG, "image/png", "bot")

    await bot.send(40, texts.BTN_BUY)
    assert bot.last(40).startswith("Введите сумму в рублях")


async def test_bot_rejects_bad_files_and_stale_confirm(bot, container, fake_tg, updates) -> None:
    await seed_rate(container, "100")
    await bot.register(41)
    # a confirm button without the collected buyer details creates nothing
    await bot.click(41, "buy:confirm:1000:9.50", message_id=7)
    assert bot.last(41) == texts.BUY_STALE
    async with container.db() as s:
        assert (await s.execute(select(Order))).scalars().all() == []

    user_id = (await orders_user(container, 41)).id
    kw = {"buyer_phone": "+79161234567", "buyer_name": "Иванов Иван"}
    created = await orders.create(container, user_id, 1000, source="bot", idempotency_key="k", **kw)
    await pay(container, created.order)

    await container.dispatcher.process(media_update(updates, 41, photo=False, size=11 * 1024 * 1024))
    assert bot.last(41) == texts.PROOF_TOO_LARGE and fake_tg.of("getFile") == []

    fake_tg.script("getFile", {"ok": True, "result": {"file_id": "doc", "file_path": "proof", "file_size": 20}})
    fake_tg.script("proof", httpx.Response(200, content=b"MZ this is not a picture"))
    await container.dispatcher.process(media_update(updates, 41, photo=False, size=20))
    assert "JPG, PNG" in bot.last(41)
    async with container.db() as s:
        assert await s.get(OrderProof, created.order.id) is None


async def orders_user(container, tg_id: int) -> User:
    async with container.db() as s:
        return (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one()
