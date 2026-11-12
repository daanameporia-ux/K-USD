from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import respx
from sqlalchemy import select

from app.models import AuditLog, Order, Staff, User
from app.services import staff as staff_svc
from app.services.actor import Actor
from tests.chain_fixtures import TRON_AMOUNT, TRON_TO, TRON_TX, mock_tron
from tests.conftest import fund, login_client, login_staff, reload, seed_rate


def idem() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def uid(container, tg_id) -> int:
    async with container.db() as s:
        return (await s.execute(select(User.id).where(User.tg_id == tg_id))).scalar_one()


async def test_withdrawal_queue_flow(api, container, clock) -> None:
    client = await login_client(api, clock, 800)
    await fund(container, await reload(container, User, await uid(container, 800)), "100")
    admin = await login_staff(api, container, clock, 801)
    await api.patch("/api/admin/settings", headers=admin, json={"changes": {"withdraw_min_usdt": "1"}})
    op = await login_staff(api, container, clock, 802, "operator")
    w = (
        await api.post(
            "/api/client/withdrawals",
            headers={**client, **idem()},
            json={"network": "TRC20", "address": TRON_TO, "amount": str(TRON_AMOUNT + 4)},
        )
    ).json()
    queue = (await api.get("/api/admin/withdrawals?status=pending", headers=op)).json()["items"]
    assert [x["id"] for x in queue] == [w["id"]] and queue[0]["user"]["tg_id"] == 800
    taken = (await api.post(f"/api/admin/withdrawals/{w['id']}/take", headers=op)).json()
    assert taken["status"] == "in_progress" and taken["assigned_staff"]["tg_id"] == 802
    second = await api.post(f"/api/admin/withdrawals/{w['id']}/take", headers=admin)
    assert second.status_code == 409
    mine = (await api.get("/api/admin/withdrawals?assigned=me&status=in_progress", headers=op)).json()["items"]
    assert len(mine) == 1
    with respx.mock:
        mock_tron(respx.mock, "wrong_amount")
        bad = (
            await api.post(f"/api/admin/withdrawals/{w['id']}/submit-hash", headers=op, json={"tx_hash": TRON_TX})
        ).json()
    assert bad["status"] == "in_progress" and bad["verify_status"] == "wrong_amount"
    assert bad["verify_label"] == "Сумма не совпадает"
    assert bad["verify_details"]["expected"]["amount"] == "8.670000"
    assert bad["verify_details"]["actual"]["amount"] == "8.670001"
    assert bad["last_explorer_url"].endswith(TRON_TX)
    with respx.mock:
        mock_tron(respx.mock, "ok")
        ok = (
            await api.post(f"/api/admin/withdrawals/{w['id']}/submit-hash", headers=op, json={"tx_hash": TRON_TX})
        ).json()
    assert ok["status"] == "completed" and ok["explorer_url"].startswith("https://tronscan.org")
    detail = (await api.get(f"/api/admin/withdrawals/{w['id']}", headers=op)).json()
    assert detail["verify_status"] == "ok"
    search = (await api.get(f"/api/admin/withdrawals?q={TRON_TX}", headers=op)).json()["items"]
    assert [x["id"] for x in search] == [w["id"]]


async def test_orders_review_and_simulation(api, container, clock) -> None:
    await seed_rate(container, "100")
    client = await login_client(api, clock, 810)
    admin = await login_staff(api, container, clock, 811)
    order = (await api.post("/api/client/orders", headers={**client, **idem()}, json={"amount_rub": "1000"})).json()
    sim = (
        await api.post(f"/api/admin/orders/{order['id']}/simulate-payment", headers=admin, json={"amount_rub": 1500})
    ).json()
    assert sim["result"] == "amount_mismatch" and sim["order"]["status"] == "review"
    listing = (await api.get("/api/admin/orders?status=review", headers=admin)).json()["items"]
    assert [o["id"] for o in listing] == [order["id"]] and listing[0]["paid_amount_rub"] == "1500"
    resolved = (
        await api.post(
            f"/api/admin/orders/{order['id']}/resolve-review", headers=admin, json={"decision": "credit_order_rate"}
        )
    ).json()
    assert resolved["status"] == "paid" and resolved["credited_usdt"] == "14.25"
    me = (await api.get("/api/client/me", headers=client)).json()
    assert me["balance"] == "14.25"
    got = (await api.get(f"/api/admin/orders/{order['id']}", headers=admin)).json()
    assert got["user"]["tg_id"] == 810
    by_q = (await api.get(f"/api/admin/orders?q={order['public_id'].lower()}", headers=admin)).json()["items"]
    assert len(by_q) == 1
    today = clock.now().date().isoformat()
    assert len((await api.get(f"/api/admin/orders?from={today}&to={today}", headers=admin)).json()["items"]) == 1


async def test_individual_markup_affects_only_new_orders(api, container, clock) -> None:
    await seed_rate(container, "100")
    client = await login_client(api, clock, 820)
    admin = await login_staff(api, container, clock, 821)
    user_id = await uid(container, 820)
    first = (await api.post("/api/client/orders", headers={**client, **idem()}, json={"amount_rub": "1000"})).json()
    patched = (
        await api.patch(
            f"/api/admin/users/{user_id}", headers=admin, json={"markup_pct": "3.5", "order_max_rub": 50000}
        )
    ).json()
    assert patched["markup_pct"] == "3.5" and patched["order_max_rub"] == 50000
    await api.post(f"/api/client/orders/{first['id']}/cancel", headers=client)
    second = (await api.post("/api/client/orders", headers={**client, **idem()}, json={"amount_rub": "1000"})).json()
    assert second["usdt_amount"] == "9.65"
    assert (await reload(container, Order, first["id"])).usdt_amount == Decimal("9.50")
    bad = await api.patch(f"/api/admin/users/{user_id}", headers=admin, json={"markup_pct": "150"})
    assert bad.status_code == 400
    async with container.db() as s:
        entry = (await s.execute(select(AuditLog).where(AuditLog.action == "user.update"))).scalars().first()
    assert entry.before == {"markup_pct": "5", "order_max_rub": None}
    assert entry.after == {"markup_pct": "3.5", "order_max_rub": 50000}


async def test_user_card_stats_ledger_and_adjust(api, container, clock) -> None:
    await login_client(api, clock, 830)
    admin = await login_staff(api, container, clock, 831)
    user_id = await uid(container, 830)
    await fund(container, await reload(container, User, user_id), "10")
    found = (await api.get("/api/admin/users?q=830", headers=admin)).json()["items"]
    assert [u["id"] for u in found] == [user_id]
    card = (await api.get(f"/api/admin/users/{user_id}", headers=admin)).json()
    assert card["balance"] == "10.00" and card["antifraud_events"] == []
    st = (await api.get(f"/api/admin/users/{user_id}/stats", headers=admin)).json()
    assert st["counts"]["paid"] == 1 and st["conversion_pct"] == "100"
    daily = (await api.get(f"/api/admin/users/{user_id}/stats/daily", headers=admin)).json()["items"]
    assert daily[0]["paid"] == 1 and daily[0]["conversion_pct"] == "100"
    neg = await api.post(
        f"/api/admin/users/{user_id}/adjust-balance", headers=admin, json={"amount": "-11", "comment": "too much minus"}
    )
    assert neg.status_code == 409
    short = await api.post(
        f"/api/admin/users/{user_id}/adjust-balance", headers=admin, json={"amount": "1", "comment": "short"}
    )
    assert short.status_code == 400
    ok = (
        await api.post(
            f"/api/admin/users/{user_id}/adjust-balance",
            headers=admin,
            json={"amount": "-2.5", "comment": "manual correction"},
        )
    ).json()
    assert ok["user"]["balance"] == "7.50" and ok["entry"]["type"] == "adjustment"
    ledger = (await api.get(f"/api/admin/users/{user_id}/ledger", headers=admin)).json()["items"]
    assert [e["type"] for e in ledger] == ["adjustment", "deposit"]
    blocked = (
        await api.post(f"/api/admin/users/{user_id}/block-orders", headers=admin, json={"comment": "suspicious"})
    ).json()
    assert blocked["can_create_orders"] is False
    assert len((await api.get("/api/admin/users?filter=af_blocked", headers=admin)).json()["items"]) == 1
    unblocked = (
        await api.post(f"/api/admin/users/{user_id}/unblock-orders", headers=admin, json={"comment": "checked"})
    ).json()
    assert unblocked["can_create_orders"] is True
    status = (await api.patch(f"/api/admin/users/{user_id}", headers=admin, json={"status": "blocked"})).json()
    assert status["status"] == "blocked"


async def test_dashboard_settings_rate_blacklist(api, container, clock) -> None:
    await seed_rate(container, "92.5")
    admin = await login_staff(api, container, clock, 840)
    dash = (await api.get("/api/admin/dashboard", headers=admin)).json()
    assert dash["rate"]["value"] == "92.50" and set(dash["periods"]) == {"today", "yesterday", "7d", "30d"}
    assert (await api.get("/api/admin/dashboard/daily", headers=admin)).json() == {"items": []}

    st = (await api.get("/api/admin/settings", headers=admin)).json()
    assert st["values"]["order_min_rub"] == 1000
    rules = {r["rule"]: r for r in st["meta"]["rules"]}
    assert set(rules) == {f"B{i}" for i in range(1, 15)} and rules["B1"]["toggle"] is None
    assert rules["B9"]["toggle"] == "b9_enabled"
    upd = (
        await api.patch(
            "/api/admin/settings", headers=admin, json={"changes": {"b9_enabled": True, "purchases_enabled": False}}
        )
    ).json()
    assert sorted(upd["changed"]) == ["b9_enabled", "purchases_enabled"]
    bad = await api.patch("/api/admin/settings", headers=admin, json={"changes": {"order_min_rub": -1}})
    assert bad.status_code == 400

    manual = (await api.post("/api/admin/rate/manual", headers=admin, json={"rate": "99,10"})).json()
    assert manual["current"] == {"value": "99.10", "source": "manual", "age_seconds": None}
    cleared = (await api.delete("/api/admin/rate/manual", headers=admin)).json()
    assert cleared["current"]["source"] == "rapira"
    assert (await api.post("/api/admin/rate/accept-jump", headers=admin)).status_code == 400

    added = (
        await api.post(
            "/api/admin/blacklist", headers=admin, json={"network": "TRC20", "address": TRON_TO, "comment": "fraud"}
        )
    ).json()
    dup = await api.post("/api/admin/blacklist", headers=admin, json={"network": "TRC20", "address": TRON_TO})
    assert dup.status_code == 409
    assert len((await api.get("/api/admin/blacklist", headers=admin)).json()["items"]) == 1
    assert (await api.delete(f"/api/admin/blacklist/{added['id']}", headers=admin)).json() == {"ok": True}
    assert (
        await api.post("/api/admin/blacklist", headers=admin, json={"network": "BEP20", "address": "0x1"})
    ).status_code == 400


async def test_staff_management_via_api(api, container, clock, fake_tg) -> None:
    admin = await login_staff(api, container, clock, 850)
    added = (await api.post("/api/admin/staff", headers=admin, json={"tg_id": 851, "role": "operator"})).json()
    assert added["invited"] is False and added["staff"]["tg_id"] == 851
    assert any(c.method == "setMyCommands" and c.params.get("scope", {}).get("chat_id") == 851 for c in fake_tg.calls)
    dup = await api.post("/api/admin/staff", headers=admin, json={"tg_id": 851, "role": "operator"})
    assert dup.status_code == 409
    inv = (await api.post("/api/admin/staff", headers=admin, json={"username": "@NewGuy", "role": "admin"})).json()
    assert inv["invited"] is True and inv["staff"]["invite_pending"] is True
    both = await api.post("/api/admin/staff", headers=admin, json={"tg_id": 1, "username": "x", "role": "admin"})
    assert both.status_code == 400
    staff_list = (await api.get("/api/admin/staff", headers=admin)).json()["items"]
    assert len(staff_list) == 3
    async with container.db() as s:
        me_id = (await s.execute(select(Staff.id).where(Staff.tg_id == 850))).scalar_one()
    assert (await api.patch(f"/api/admin/staff/{me_id}", headers=admin, json={"role": "operator"})).status_code == 403
    assert (await api.patch(f"/api/admin/staff/{me_id}", headers=admin, json={"is_active": False})).status_code == 403
    promoted = (
        await api.patch(f"/api/admin/staff/{added['staff']['id']}", headers=admin, json={"role": "admin"})
    ).json()
    assert promoted["role"] == "admin"
    assert (await api.delete(f"/api/admin/staff/{inv['staff']['id']}", headers=admin)).json() == {"ok": True}
    assert len((await api.get("/api/admin/staff", headers=admin)).json()["items"]) == 2


async def test_audit_visibility(api, container, clock) -> None:
    admin = await login_staff(api, container, clock, 860)
    op = await login_staff(api, container, clock, 861, "operator")
    await api.patch("/api/admin/settings", headers=admin, json={"changes": {"order_ttl_minutes": 20}})
    await api.post("/api/admin/profile/web-credentials", headers=op, json={"login": "op861", "password": "x" * 12})
    all_entries = (await api.get("/api/admin/audit", headers=admin)).json()["items"]
    assert {e["action"] for e in all_entries} >= {"settings.update", "staff.set_web_credentials"}
    own = (await api.get("/api/admin/audit", headers=op)).json()["items"]
    assert [e["action"] for e in own] == ["staff.set_web_credentials"]
    async with container.db() as s:
        admin_id = (await s.execute(select(Staff.id).where(Staff.tg_id == 860))).scalar_one()
    forced = (await api.get(f"/api/admin/audit?staff_id={admin_id}", headers=op)).json()["items"]
    assert [e["action"] for e in forced] == ["staff.set_web_credentials"]
    filtered = (
        await api.get("/api/admin/audit?entity_type=setting&entity_id=order_ttl_minutes", headers=admin)
    ).json()["items"]
    assert filtered[0]["before"] == {"value": 15} and filtered[0]["after"] == {"value": 20}


async def test_pagination_cursor(api, container, clock) -> None:
    admin = await login_staff(api, container, clock, 870)
    for i in range(5):
        await login_client(api, clock, 871 + i, register=False)
    page1 = (await api.get("/api/admin/users?limit=2", headers=admin)).json()
    page2 = (await api.get(f"/api/admin/users?limit=2&cursor={page1['next_cursor']}", headers=admin)).json()
    page3 = (await api.get(f"/api/admin/users?limit=2&cursor={page2['next_cursor']}", headers=admin)).json()
    ids = [u["id"] for p in (page1, page2, page3) for u in p["items"]]
    assert len(ids) == 5 == len(set(ids)) and page3["next_cursor"] is None
    assert (await api.get("/api/admin/users?cursor=abc", headers=admin)).status_code == 400


async def test_staff_service_rules(container, clock) -> None:
    async with container.db.begin() as s:
        await staff_svc.sync_superadmins(s, [1000], clock.now())
    async with container.db() as s:
        sup = (await s.execute(select(Staff).where(Staff.tg_id == 1000))).scalar_one()
    actor = Actor(sup.id, sup.role, True, 1000)
    other_admin = (await staff_svc.add(container, actor, role="admin", tg_id=1001)).staff
    other = Actor(other_admin.id, other_admin.role)
    from app.domain.errors import ServiceError

    for call in (
        staff_svc.remove(container, other, sup.id),
        staff_svc.update(container, other, sup.id, role="operator"),
        staff_svc.update(container, other, sup.id, is_active=False),
    ):
        try:
            await call
        except ServiceError as exc:
            assert exc.status == 403
        else:
            raise AssertionError("superadmin must be protected")
    # deactivated by removal: record kept because it is referenced by audit
    await staff_svc.remove(container, actor, other_admin.id)
    assert (await reload(container, Staff, other_admin.id)).is_active is False


async def test_invite_expiry(container, clock) -> None:
    async with container.db.begin() as s:
        await staff_svc.sync_superadmins(s, [1000], clock.now())
        sup = (await s.execute(select(Staff).where(Staff.tg_id == 1000))).scalar_one()
    actor = Actor(sup.id, sup.role, True, 1000)
    inv = (await staff_svc.add(container, actor, role="operator", username="late_guy")).staff
    clock.advance(hours=49)
    assert await staff_svc.bind_invite(container, 5555, "late_guy") is None
    assert await staff_svc.expire_invites(container) == 1
    assert await reload(container, Staff, inv.id) is None


async def test_add_by_username_of_known_user(container, clock) -> None:
    from tests.conftest import make_user

    async with container.db.begin() as s:
        await staff_svc.sync_superadmins(s, [1000], clock.now())
        sup = (await s.execute(select(Staff).where(Staff.tg_id == 1000))).scalar_one()
    await make_user(container, tg_id=4242, tg_username="KnownUser")
    result = await staff_svc.add(container, Actor(sup.id, sup.role, True), role="operator", username="@knownuser")
    assert result.invited is False and result.staff.tg_id == 4242
    _ = timedelta
