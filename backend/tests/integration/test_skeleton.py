from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import ConfigError, load_config
from app.main import create_app


async def test_healthz(config) -> None:
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_config_refuses_missing_required(monkeypatch) -> None:
    monkeypatch.delenv("BOT_TOKEN")
    with pytest.raises(ConfigError) as exc:
        load_config(_env_file=None)
    assert "BOT_TOKEN" in str(exc.value)


def test_config_mock_forbidden_in_prod(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(ConfigError) as exc:
        load_config(_env_file=None)
    assert "ALLOW_MOCK_IN_PROD" in str(exc.value)
    monkeypatch.setenv("ALLOW_MOCK_IN_PROD", "true")
    assert load_config(_env_file=None).env == "prod"


def test_config_parses_lists(monkeypatch) -> None:
    monkeypatch.setenv("SUPERADMIN_TG_IDS", "1, 2,3")
    monkeypatch.setenv("BSC_RPC_URLS", "https://a, https://b")
    monkeypatch.setenv("STAFF_CHAT_ID", "")
    cfg = load_config(_env_file=None)
    assert cfg.superadmin_tg_ids == [1, 2, 3]
    assert cfg.bsc_rpc_urls == ["https://a", "https://b"]
    assert cfg.staff_chat_id is None
    assert cfg.database_url.startswith("postgresql+asyncpg://")


async def test_db_constraints_present(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (tg_id, status, kyc_status, markup_pct) VALUES (1, 'active', 'passed', 5)"
            )
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE users SET balance = -1 WHERE tg_id = 1"))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE users SET hold = -1 WHERE tg_id = 1"))
    async with engine.begin() as conn:
        uid = (await conn.execute(text("SELECT id FROM users"))).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO orders (public_id, user_id, amount_rub, rate, rate_source, markup_pct, usdt_amount,"
                " margin_rub, status, provider, source, expires_at)"
                " VALUES ('B-1', :u, 1000, 100, 'rapira', 5, 9.5, 50, 'pending', 'mock', 'bot', now())"
            ),
            {"u": uid},
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO orders (public_id, user_id, amount_rub, rate, rate_source, markup_pct,"
                    " usdt_amount, margin_rub, status, provider, source, expires_at)"
                    " VALUES ('B-2', :u, 1000, 100, 'rapira', 5, 9.5, 50, 'creating', 'mock', 'bot', now())"
                ),
                {"u": uid},
            )
