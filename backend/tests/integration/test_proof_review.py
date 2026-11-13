"""Staff reviews the payment screenshots of all paid orders of a client before a withdrawal moves on."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.jobs.outbox import send_notifications
from app.models import Order, OrderProof, User, Withdrawal
from tests.chain_fixtures import TRON_TO, TRON_TX
from tests.conftest import login_client, login_staff, pay, seed_rate

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
BUYER = {"buyer_phone": "+79161234567", "buyer_name": "Иванов Иван Иванович"}

pytestmark = pytest.mark.buyer_details


def idem() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def buy(api, container, h, *, upload: bool = True) -> int:
    resp = await api.post("/api/client/orders", headers={**h, **idem()}, json={"amount_rub": "100000", **BUYER})
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["id"]
    async with container.db() as s:
        order = await s.get(Order, order_id)
    await pay(container, order)
    if upload:
        assert (await api.post(f"/api/client/orders/{order_id}/proof", headers=h, content=PNG)).status_code == 200
    return order_id


async def withdraw(api, h, amount="100") -> int:
    body = {"network": "TRC20", "address": TRON_TO, "amount": amount}
    resp = await api.post("/api/client/withdrawals", headers={**h, **idem()}, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def no_b11(container) -> None:
    """Fresh deposits are withdrawable in these tests."""
    from app.services import settings as settings_svc

    async with container.db.begin() as s:
        await settings_svc.update(s, {"b11_enabled": False}, staff_id=None, now=container.clock.now())


async def test_withdrawal_waits_for_all_screenshots_to_be_reviewed(api, container, clock) -> None:
    await seed_rate(container, "100")
    await no_b11(container)
    h = await login_client(api, clock, 950)
    first = await buy(api, container, h)
    second = await buy(api, container, h)
    wid = await withdraw(api, h)
    operator = await login_staff(api, container, clock, 9500, role="operator")

    card = (await api.get(f"/api/admin/withdrawals/{wid}", headers=operator)).json()
    assert card["proofs"]["to_review"] == 2 and card["proofs"]["missing"] == 0 and card["proofs"]["blocking"] == 2
    assert [o["id"] for o in card["proofs"]["orders"]] == [second, first]
    assert card["proofs"]["orders"][0]["buyer_name"] == "Иванов Иван Иванович"

    blocked = await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "proofs_not_reviewed"
    assert blocked.json()["error"]["details"] == {"to_review": 2, "missing": 0}

    ok = await api.post(f"/api/admin/orders/{first}/proof/review", headers=operator)
    assert ok.status_code == 200 and ok.json()["proof_reviewed_at"] is not None
    assert (
        await api.post(f"/api/admin/orders/{first}/proof/review", headers=operator)
    ).status_code == 200  # idempotent
    still = await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)
    assert still.status_code == 409 and still.json()["error"]["details"] == {"to_review": 1, "missing": 0}

    assert (await api.post(f"/api/admin/orders/{second}/proof/review", headers=operator)).status_code == 200
    taken = await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)
    assert taken.status_code == 200 and taken.json()["status"] == "in_progress"
    assert taken.json()["proofs"] == {"to_review": 0, "missing": 0, "reviewed": 2, "blocking": 0, "orders": []}

    admin = await login_staff(api, container, clock, 9501)
    actions = [i["action"] for i in (await api.get("/api/admin/audit", headers=admin)).json()["items"]]
    assert actions.count("order.proof_review") == 2

    # a new paid order after `take`: its screenshot must be reviewed before the hash is accepted
    third = await buy(api, container, h)
    late = await api.post(f"/api/admin/withdrawals/{wid}/submit-hash", headers=operator, json={"tx_hash": TRON_TX})
    assert late.status_code == 409 and late.json()["error"]["code"] == "proofs_not_reviewed"
    forced = await api.post(f"/api/admin/withdrawals/{wid}/force-complete", headers=admin, json={"comment": "вручную"})
    assert forced.status_code == 409 and forced.json()["error"]["code"] == "proofs_not_reviewed"
    # rejecting the withdrawal is always possible
    assert (await api.post(f"/api/admin/orders/{third}/proof/review", headers=operator)).status_code == 200
    done = await api.post(f"/api/admin/withdrawals/{wid}/submit-hash", headers=operator, json={"tx_hash": TRON_TX})
    assert done.status_code == 200 and done.json()["hash_submitted_at"] is not None  # the gate is passed


async def test_rejected_screenshot_goes_back_to_the_client(api, container, clock, bot) -> None:
    await seed_rate(container, "100")
    await no_b11(container)
    h = await login_client(api, clock, 951)
    order_id = await buy(api, container, h)
    wid = await withdraw(api, h)
    operator = await login_staff(api, container, clock, 9502, role="operator")

    assert (
        await api.post(f"/api/admin/orders/{order_id}/proof/reject", headers=operator, json={"comment": ""})
    ).status_code == 400
    rejected = await api.post(
        f"/api/admin/orders/{order_id}/proof/reject", headers=operator, json={"comment": "Не видно <прошлых> операций"}
    )
    assert rejected.status_code == 200 and rejected.json()["proof_uploaded_at"] is None
    async with container.db() as s:
        assert await s.get(OrderProof, order_id) is None
    await send_notifications(container)
    assert "не принят" in bot.last(951) and "Не видно &lt;прошлых&gt; операций" in bot.last(951)

    card = (await api.get(f"/api/admin/withdrawals/{wid}", headers=operator)).json()
    assert card["proofs"]["missing"] == 1 and card["proofs"]["to_review"] == 0
    blocked = await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)
    assert blocked.status_code == 409 and "не загрузил" in blocked.json()["error"]["message"]
    nothing = await api.post(f"/api/admin/orders/{order_id}/proof/review", headers=operator)
    assert nothing.status_code == 409 and nothing.json()["error"]["code"] == "proof_missing"

    # the client: no new orders and no new withdrawals until the screenshot is sent again
    me = (await api.get("/api/client/me", headers=h)).json()
    assert me["proof_pending_order"]["id"] == order_id
    body = {"network": "TRC20", "address": TRON_TO, "amount": "100"}
    for path, extra in [("/api/client/withdrawals/preview", {}), ("/api/client/withdrawals", idem())]:
        resp = await api.post(path, headers={**h, **extra}, json=body)
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "proof_required", resp.text

    assert (await api.post(f"/api/client/orders/{order_id}/proof", headers=h, content=PNG)).status_code == 200
    assert (await api.post(f"/api/admin/orders/{order_id}/proof/review", headers=operator)).status_code == 200
    # a replaced screenshot must be looked at again
    assert (await api.post(f"/api/client/orders/{order_id}/proof", headers=h, content=PNG)).status_code == 200
    again = await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)
    assert again.status_code == 409 and again.json()["error"]["details"] == {"to_review": 1, "missing": 0}
    assert (await api.post(f"/api/admin/orders/{order_id}/proof/review", headers=operator)).status_code == 200
    assert (await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)).status_code == 200


async def test_review_needs_staff_and_skips_clients_without_the_requirement(api, container, clock) -> None:
    await seed_rate(container, "100")
    await no_b11(container)
    h = await login_client(api, clock, 952)
    order_id = await buy(api, container, h)
    assert (await api.post(f"/api/admin/orders/{order_id}/proof/review", headers=h)).status_code == 403
    assert (await api.post(f"/api/admin/orders/{order_id}/proof/review")).status_code == 401

    # the requirement switched off for a client: nothing to upload, nothing to review
    free = await login_client(api, clock, 953)
    async with container.db.begin() as s:
        user = (await s.execute(select(User).where(User.tg_id == 953))).scalar_one()
        user.require_payment_proof = False
    await buy(api, container, free, upload=False)
    wid = await withdraw(api, free)
    operator = await login_staff(api, container, clock, 9503, role="operator")
    taken = await api.post(f"/api/admin/withdrawals/{wid}/take", headers=operator)
    assert taken.status_code == 200 and taken.json()["proofs"]["blocking"] == 0
    async with container.db() as s:
        assert (await s.get(Withdrawal, wid)).assigned_staff_id is not None
