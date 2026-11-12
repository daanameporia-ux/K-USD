from __future__ import annotations

from datetime import timedelta

import pyotp
from sqlalchemy import select

from app.models import AuthSession, Staff, User
from tests.conftest import init_data, login_client, login_staff


async def test_client_telegram_auth_creates_user(api, container, clock) -> None:
    resp = await api.post(
        "/api/auth/telegram", json={"init_data": init_data(501, clock.now(), "neo"), "scope": "client"}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    async with container.db() as s:
        user = (await s.execute(select(User).where(User.tg_id == 501))).scalar_one()
        sess = (await s.execute(select(AuthSession))).scalar_one()
    assert user.tg_username == "neo" and user.status.value == "registering"
    assert sess.token_hash != token and len(sess.token_hash) == 64
    me = await api.get("/api/client/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["registered"] is False
    reg = await api.post("/api/client/register", headers={"Authorization": f"Bearer {token}"})
    assert reg.json()["step"]["status"] == "passed" and reg.json()["me"]["registered"] is True


async def test_bad_init_data(api, clock) -> None:
    bad = init_data(501, clock.now()).replace("U", "X")
    resp = await api.post("/api/auth/telegram", json={"init_data": bad, "scope": "client"})
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"
    old = init_data(501, clock.now() - timedelta(minutes=11))
    assert (await api.post("/api/auth/telegram", json={"init_data": old, "scope": "client"})).status_code == 401


async def test_admin_scope_requires_staff(api, container, clock) -> None:
    resp = await api.post("/api/auth/telegram", json={"init_data": init_data(777, clock.now()), "scope": "admin"})
    assert resp.status_code == 403
    headers = await login_staff(api, container, clock, 778, "operator")
    me = await api.get("/api/admin/me", headers=headers)
    assert me.status_code == 200 and me.json()["staff"]["role"] == "operator" and me.json()["is_admin"] is False


async def test_sessions_expiry_and_logout(api, container, clock) -> None:
    client = await login_client(api, clock, 502)
    clock.advance(hours=11)
    assert (await api.get("/api/client/me", headers=client)).status_code == 200  # activity extends
    clock.advance(hours=11)
    assert (await api.get("/api/client/me", headers=client)).status_code == 200
    clock.advance(hours=12, seconds=1)
    resp = await api.get("/api/client/me", headers=client)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "session_expired"

    staff = await login_staff(api, container, clock, 779)
    clock.advance(minutes=59)
    assert (await api.get("/api/admin/me", headers=staff)).status_code == 200
    clock.advance(minutes=61)
    assert (await api.get("/api/admin/me", headers=staff)).status_code == 401  # idle > 60 min

    staff = await login_staff(api, container, clock, 779)
    for _ in range(9):
        clock.advance(minutes=55)
        await api.get("/api/admin/me", headers=staff)
    assert (await api.get("/api/admin/me", headers=staff)).status_code == 401  # absolute 8h

    client = await login_client(api, clock, 503)
    assert (await api.post("/api/auth/logout", headers=client)).status_code == 200
    assert (await api.get("/api/client/me", headers=client)).status_code == 401
    assert (await api.get("/api/client/me", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await api.get("/api/client/me", headers={"Authorization": "Basic x"})).status_code == 401


async def setup_web_access(api, container, clock, tg_id=780, login="operator.one", password="correct horse battery"):
    headers = await login_staff(api, container, clock, tg_id)
    resp = await api.post(
        "/api/admin/profile/web-credentials", headers=headers, json={"login": login, "password": password}
    )
    assert resp.status_code == 200, resp.text
    setup = (await api.post("/api/admin/profile/totp/setup", headers=headers)).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and setup["qr_png_base64"]
    return headers, setup["secret"]


async def test_password_login_requires_totp(api, container, clock) -> None:
    headers = await login_staff(api, container, clock, 781)
    short = await api.post(
        "/api/admin/profile/web-credentials", headers=headers, json={"login": "abc", "password": "short"}
    )
    assert short.status_code == 400 and "12" in short.json()["error"]["message"]
    await api.post(
        "/api/admin/profile/web-credentials",
        headers=headers,
        json={"login": "admin.web", "password": "correct horse battery"},
    )
    resp = await api.post(
        "/api/auth/password", json={"login": "admin.web", "password": "correct horse battery", "totp": "123456"}
    )
    assert resp.status_code == 401 and resp.json()["error"]["message"] == "Неверный логин, пароль или код."


async def test_password_login_flow(api, container, clock) -> None:
    headers, secret = await setup_web_access(api, container, clock)
    totp = pyotp.TOTP(secret)
    bad = await api.post("/api/admin/profile/totp/confirm", headers=headers, json={"code": "000000"})
    assert bad.status_code == 400
    ok = await api.post("/api/admin/profile/totp/confirm", headers=headers, json={"code": totp.at(clock.now())})
    assert ok.json()["totp_enabled"] is True

    clock.advance(seconds=30)
    code = totp.at(clock.now())
    resp = await api.post(
        "/api/auth/password", json={"login": "Operator.One", "password": "correct horse battery", "totp": code}
    )
    assert resp.status_code == 200
    web = {"Authorization": f"Bearer {resp.json()['token']}"}
    me = (await api.get("/api/admin/me", headers=web)).json()
    assert me["auth_method"] == "password"
    # the same code cannot be reused within its window
    again = await api.post(
        "/api/auth/password", json={"login": "operator.one", "password": "correct horse battery", "totp": code}
    )
    assert again.status_code == 401
    # credentials can only be changed from the Telegram Mini App
    resp = await api.post("/api/admin/profile/totp/setup", headers=web)
    assert resp.status_code == 403


async def test_bruteforce_lockout(api, container, clock) -> None:
    _, secret = await setup_web_access(api, container, clock, tg_id=782, login="victim")
    totp = pyotp.TOTP(secret)
    async with container.db.begin() as s:
        staff = (await s.execute(select(Staff).where(Staff.login == "victim"))).scalar_one()
        staff.totp_enabled = True
    for i in range(5):
        resp = await api.post("/api/auth/password", json={"login": "victim", "password": f"wrong-{i}", "totp": "1"})
        assert resp.status_code == 401
    resp = await api.post(
        "/api/auth/password",
        json={"login": "victim", "password": "correct horse battery", "totp": totp.at(clock.now())},
    )
    assert resp.status_code == 429
    # unknown login answers exactly like a wrong password
    clock.advance(minutes=16)
    unknown = await api.post(
        "/api/auth/password",
        json={"login": "ghost", "password": "x" * 12, "totp": "123456"},
        headers={"X-Forwarded-For": "10.0.0.9"},
    )
    assert unknown.status_code == 401 and unknown.json()["error"]["message"] == "Неверный логин, пароль или код."
    resp = await api.post(
        "/api/auth/password",
        json={"login": "victim", "password": "correct horse battery", "totp": totp.at(clock.now())},
    )
    assert resp.status_code == 200


async def test_ip_lockout(api, container, clock) -> None:
    for i in range(5):
        await api.post(
            "/api/auth/password",
            json={"login": f"user{i}", "password": "x", "totp": "1"},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
    resp = await api.post(
        "/api/auth/password",
        json={"login": "other", "password": "x", "totp": "1"},
        headers={"X-Forwarded-For": "1.1.1.1"},
    )
    assert resp.status_code == 429


async def test_deactivated_staff_loses_session(api, container, clock) -> None:
    admin = await login_staff(api, container, clock, 1000)  # superadmin id from env (not synced in tests)
    op = await login_staff(api, container, clock, 783, "operator")
    async with container.db() as s:
        op_id = (await s.execute(select(Staff.id).where(Staff.tg_id == 783))).scalar_one()
    resp = await api.patch(f"/api/admin/staff/{op_id}", headers=admin, json={"is_active": False})
    assert resp.status_code == 200
    assert (await api.get("/api/admin/me", headers=op)).status_code == 401
