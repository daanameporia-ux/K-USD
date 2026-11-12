"""Every admin endpoint: no session -> 401, client session -> 403, operator on admin-only -> 403."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from app.api.deps import admin_auth
from app.main import create_app
from tests.conftest import login_client, login_staff

ADMIN_ONLY = {
    ("POST", "/api/admin/withdrawals/{withdrawal_id}/force-complete"),
    ("POST", "/api/admin/orders/{order_id}/resolve-review"),
    ("POST", "/api/admin/orders/{order_id}/simulate-payment"),
    ("PATCH", "/api/admin/users/{user_id}"),
    ("POST", "/api/admin/users/{user_id}/unblock-orders"),
    ("POST", "/api/admin/users/{user_id}/block-orders"),
    ("POST", "/api/admin/users/{user_id}/adjust-balance"),
    ("GET", "/api/admin/dashboard"),
    ("GET", "/api/admin/dashboard/daily"),
    ("POST", "/api/admin/antifraud/events/{event_id}/resolve"),
    ("GET", "/api/admin/settings"),
    ("PATCH", "/api/admin/settings"),
    ("POST", "/api/admin/rate/manual"),
    ("DELETE", "/api/admin/rate/manual"),
    ("POST", "/api/admin/rate/accept-jump"),
    ("GET", "/api/admin/blacklist"),
    ("POST", "/api/admin/blacklist"),
    ("DELETE", "/api/admin/blacklist/{entry_id}"),
    ("GET", "/api/admin/staff"),
    ("POST", "/api/admin/staff"),
    ("PATCH", "/api/admin/staff/{staff_id}"),
    ("DELETE", "/api/admin/staff/{staff_id}"),
}


def _uses_admin_auth(route: APIRoute) -> bool:
    def walk(dep) -> bool:
        return any(d.call is admin_auth or walk(d) for d in dep.dependencies)

    return walk(route.dependant)


def admin_routes(config) -> list[tuple[str, str, bool]]:
    from app.api import admin

    create_app(config)  # the router is the one mounted into the app
    out = []
    for route in admin.router.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/admin"):
            for method in sorted(route.methods):
                out.append((method, route.path, _uses_admin_auth(route)))
    return out


def test_admin_only_set_matches_spec(config) -> None:
    routes = admin_routes(config)
    assert len(routes) >= 40
    assert {(m, p) for m, p, admin in routes if admin} == ADMIN_ONLY


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "999999", path)


BODIES = {
    "submit-hash": {"tx_hash": "ab" * 32},
    "reject": {"reason": "test reason"},
    "force-complete": {"comment": "test comment"},
    "resolve-review": {"decision": "reject", "comment": "x"},
    "adjust-balance": {"amount": "1", "comment": "long enough comment"},
    "unblock-orders": {"comment": "test"},
    "block-orders": {"comment": "test"},
    "settings": {"changes": {}},
    "manual": {"rate": "90"},
    "blacklist": {"network": "TRC20", "address": "TGfb53j2wY8J2F7J5RQhs6gdKpC2j6zsBY"},
    "staff": {"tg_id": 123456, "role": "operator"},
    "web-credentials": {"login": "someone", "password": "x" * 12},
    "confirm": {"code": "123456"},
}


def body_for(path: str):
    for key, body in BODIES.items():
        if path.rstrip("/").endswith(key) or f"/{key}/" in path:
            return body
    return {}


@pytest.fixture
async def sessions(api, container, clock):
    return {
        "client": await login_client(api, clock, 601),
        "operator": await login_staff(api, container, clock, 602, "operator"),
    }


async def test_permissions_for_every_admin_endpoint(api, config, sessions) -> None:
    checked = 0
    for method, path, admin_only in admin_routes(config):
        url = concrete(path)
        kw = {"json": body_for(path)} if method in ("POST", "PATCH") else {}
        anon = await api.request(method, url, **kw)
        assert anon.status_code == 401, (method, path, anon.status_code)
        assert anon.json()["error"]["code"] == "unauthorized"
        client = await api.request(method, url, headers=sessions["client"], **kw)
        assert client.status_code == 403, (method, path, client.status_code)
        op = await api.request(method, url, headers=sessions["operator"], **kw)
        if admin_only:
            assert op.status_code == 403, (method, path, op.status_code)
        else:
            assert op.status_code not in (401, 403), (method, path, op.status_code, op.text)
        checked += 1
    assert checked >= 40


async def test_client_endpoints_require_client_session(api, container, clock) -> None:
    staff = await login_staff(api, container, clock, 603, "admin")
    for method, url in [("GET", "/api/client/me"), ("POST", "/api/client/orders"), ("GET", "/api/client/history")]:
        assert (await api.request(method, url)).status_code == 401
        assert (await api.request(method, url, headers=staff)).status_code == 403
