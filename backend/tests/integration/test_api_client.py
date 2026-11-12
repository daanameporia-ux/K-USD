from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import User
from tests.chain_fixtures import TRON_TO
from tests.conftest import fund, login_client, seed_rate


def idem() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def user_of(container, tg_id) -> User:
    async with container.db() as s:
        return (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one()


async def test_purchase_flow_via_api(api, container, clock) -> None:
    await seed_rate(container, "100")
    h = await login_client(api, clock, 700)
    me = (await api.get("/api/client/me", headers=h)).json()
    assert me["balance"] == "0.00" and me["limits"] == {"min_rub": "1000", "max_rub": "100000"}
    rate = (await api.get("/api/client/rate", headers=h)).json()
    assert rate == {
        "rate": "100.00",
        "source": "rapira",
        "age_seconds": 0,
        "markup_pct": "5",
        "effective_rate": "105.27",
    }

    pv = await api.post("/api/client/orders/preview", headers=h, json={"amount_rub": "1000,5"})
    assert (
        pv.status_code == 400 and pv.json()["error"]["message"] == "Сумма должна быть целым числом рублей, без копеек."
    )
    pv = (await api.post("/api/client/orders/preview", headers=h, json={"amount_rub": 1000})).json()
    assert pv["usdt_amount"] == "9.50" and pv["fee_rub"] == "50" and isinstance(pv["amount_rub"], str)

    no_key = await api.post("/api/client/orders", headers=h, json={"amount_rub": "1000"})
    assert no_key.status_code == 400 and no_key.json()["error"]["code"] == "idempotency_key_required"
    key = idem()
    created = (
        await api.post("/api/client/orders", headers={**h, **key}, json={"amount_rub": "1000", "expected_usdt": "9.50"})
    ).json()
    assert created["status"] == "pending" and created["created"] is True and created["usdt_amount"] == "9.50"
    again = (await api.post("/api/client/orders", headers={**h, **key}, json={"amount_rub": "1000"})).json()
    assert again["id"] == created["id"] and again["created"] is False
    conflict = await api.post("/api/client/orders", headers={**h, **idem()}, json={"amount_rub": "1000"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "active_order_exists"

    active = (await api.get("/api/client/orders/active", headers=h)).json()["order"]
    assert active["id"] == created["id"]
    one = (await api.get(f"/api/client/orders/{created['id']}", headers=h)).json()
    assert one["public_id"] == created["public_id"]
    qr = await api.get(f"/api/client/orders/{created['id']}/qr.png", headers=h)
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/png" and qr.content.startswith(b"\x89PNG")

    cancelled = (await api.post(f"/api/client/orders/{created['id']}/cancel", headers=h)).json()
    assert cancelled["status"] == "cancelled"
    assert (await api.get("/api/client/orders/active", headers=h)).json()["order"] is None
    hist = (await api.get("/api/client/history", headers=h)).json()
    assert hist["items"][0]["public_id"] == created["public_id"] and hist["next_cursor"] is None


async def test_price_changed_via_api(api, container, clock) -> None:
    await seed_rate(container, "100")
    h = await login_client(api, clock, 701)
    resp = await api.post(
        "/api/client/orders", headers={**h, **idem()}, json={"amount_rub": "1000", "expected_usdt": "9.40"}
    )
    assert resp.status_code == 409 and resp.json()["error"]["details"]["usdt_amount"] == "9.50"


async def test_withdraw_flow_via_api(api, container, clock) -> None:
    h = await login_client(api, clock, 702)
    await fund(container, await user_of(container, 702), "100")
    cfg = (await api.get("/api/client/withdrawals/config", headers=h)).json()
    assert cfg["available"] == "100.00" and cfg["networks"][0] == {"network": "TRC20", "label": "TRC-20", "fee": "4.00"}
    bad = await api.post(
        "/api/client/withdrawals/preview", headers=h, json={"network": "TRC20", "address": "Tbad", "amount": "60"}
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_address"
    pv = (
        await api.post(
            "/api/client/withdrawals/preview", headers=h, json={"network": "TRC20", "address": TRON_TO, "amount": "60"}
        )
    ).json()
    assert pv["amount_to_send"] == "56.00"
    key = idem()
    w = (
        await api.post(
            "/api/client/withdrawals",
            headers={**h, **key},
            json={"network": "TRC20", "address": TRON_TO, "amount": "60"},
        )
    ).json()
    assert w["status"] == "pending" and w["amount"] == "60.00"
    again = (
        await api.post(
            "/api/client/withdrawals",
            headers={**h, **key},
            json={"network": "TRC20", "address": TRON_TO, "amount": "60"},
        )
    ).json()
    assert again["id"] == w["id"] and again["created"] is False
    me = (await api.get("/api/client/me", headers=h)).json()
    assert (me["balance"], me["hold"]) == ("40.00", "60.00")
    assert (await api.post(f"/api/client/withdrawals/{w['id']}/cancel", headers=h)).json()["status"] == "cancelled"
    too_much = await api.post(
        "/api/client/withdrawals",
        headers={**h, **idem()},
        json={"network": "TRC20", "address": TRON_TO, "amount": "1000"},
    )
    assert too_much.status_code == 409


async def test_isolation_between_clients(api, container, clock) -> None:
    await seed_rate(container, "100")
    a = await login_client(api, clock, 703)
    b = await login_client(api, clock, 704)
    order = (await api.post("/api/client/orders", headers={**a, **idem()}, json={"amount_rub": "1000"})).json()
    await fund(container, await user_of(container, 703), "100")
    w = (
        await api.post(
            "/api/client/withdrawals",
            headers={**a, **idem()},
            json={"network": "TRC20", "address": TRON_TO, "amount": "60"},
        )
    ).json()
    for method, url in [
        ("GET", f"/api/client/orders/{order['id']}"),
        ("GET", f"/api/client/orders/{order['id']}/qr.png"),
        ("POST", f"/api/client/orders/{order['id']}/cancel"),
        ("POST", f"/api/client/withdrawals/{w['id']}/cancel"),
    ]:
        resp = await api.request(method, url, headers=b)
        assert resp.status_code == 404, url
    assert (await api.get("/api/client/history", headers=b)).json()["items"] == []
    assert (await api.get("/api/client/orders/active", headers=b)).json()["order"] is None


async def test_unregistered_client_cannot_trade(api, container, clock) -> None:
    await seed_rate(container, "100")
    h = await login_client(api, clock, 705, register=False)
    resp = await api.post("/api/client/orders/preview", headers=h, json={"amount_rub": "1000"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "registration_required"


async def test_money_rate_limit(api, container, clock) -> None:
    h = await login_client(api, clock, 706)
    codes = []
    for _ in range(11):
        resp = await api.post(
            "/api/client/withdrawals",
            headers={**h, **idem()},
            json={"network": "TRC20", "address": TRON_TO, "amount": "60"},
        )
        codes.append(resp.status_code)
    assert codes[:10] == [409] * 10 and codes[10] == 429


async def test_session_rate_limit(api, container, clock) -> None:
    h = await login_client(api, clock, 707)
    codes = [(await api.get("/api/client/me", headers=h)).status_code for _ in range(121)]
    assert codes.count(429) >= 1
    clock.advance(seconds=61)
    assert (await api.get("/api/client/me", headers=h)).status_code == 200


async def test_errors_and_security_headers(api, clock) -> None:
    resp = await api.get("/api/nope")
    assert resp.status_code == 404 and resp.json() == {"error": {"code": "not_found", "message": "Не найдено"}}
    h = await login_client(api, clock, 708)
    resp = await api.post("/api/client/withdrawals/preview", headers=h, json={"network": "XXX"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "validation_error"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors" in resp.headers["content-security-policy"]
    assert "https://telegram.org" in resp.headers["content-security-policy"]
    assert "access-control-allow-origin" not in resp.headers
    health = await api.get("/healthz")
    assert health.json() == {"status": "ok"}
    ready = await api.get("/readyz")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] is True
