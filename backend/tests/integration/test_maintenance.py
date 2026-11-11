from __future__ import annotations

from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.jobs import maintenance
from app.jobs.registry import build_scheduler
from app.models import AuthSession, TgUpdate
from app.services import settings as settings_svc
from tests.conftest import fund, make_user, outbox, seed_rate


async def test_ledger_invariant_job_disables_withdrawals(container) -> None:
    user = await make_user(container)
    await fund(container, user, "10")
    assert await maintenance.check_ledger_invariant(container) == 0
    async with container.db.begin() as s:
        await s.execute(text("UPDATE users SET balance = 1000"))
    assert await maintenance.check_ledger_invariant(container) == 1
    async with container.db() as s:
        assert (await settings_svc.load(s)).withdrawals_enabled is False
    assert any("КРИТИЧНО" in m["text"] for m in await outbox(container, 0))


async def test_cleanup_job(container, clock, updates, bot) -> None:
    await bot.send(1, "/start")
    await seed_rate(container, "100")
    from tests.conftest import login_client  # noqa: F401

    clock.advance(days=40)
    async with container.db.begin() as s:
        s.add(TgUpdate(update_id=10**9, received_at=clock.now() - timedelta(days=1)))
    await maintenance.cleanup(container)
    async with container.db() as s:
        assert (await s.execute(select(func.count()).select_from(TgUpdate))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(AuthSession))).scalar() == 0


async def test_registry_contains_all_jobs(container) -> None:
    names = {j.name for j in build_scheduler(container).jobs}
    assert names == {
        "refresh_rate",
        "expire_orders",
        "fail_stuck_creating",
        "verify_withdrawals",
        "send_notifications",
        "expire_invites",
        "check_ledger_invariant",
        "cleanup",
        "global_conversion_watch",
    }


async def test_app_lifespan_startup_and_shutdown(config, engine, fake_tg, monkeypatch) -> None:
    """Own container: superadmins synced, bot configured, scheduler started and stopped."""
    import httpx

    from app import main as main_mod
    from app.container import Container
    from app.models import Staff

    real_init = Container.__init__

    def init(self, cfg, **kw):
        real_init(
            self,
            cfg,
            tg_transport=fake_tg.transport(),
            engine=engine,
            http_transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        )

    monkeypatch.setattr(Container, "__init__", init)
    monkeypatch.setattr(config, "run_scheduler", True)
    app = main_mod.create_app(config)
    async with app.router.lifespan_context(app):
        c = app.state.container
        assert c.scheduler.running
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            ready = await client.get("/readyz")
        assert ready.json()["checks"]["scheduler"] is True
    assert not c.scheduler.running
    assert [m.method for m in fake_tg.calls][:3] == ["setWebhook", "setMyCommands", "setChatMenuButton"]
    async with c.db() as s:
        sup = (await s.execute(select(Staff).where(Staff.tg_id == 1000))).scalar_one()
    assert sup.is_superadmin and sup.role.value == "admin"


async def test_polling_mode_startup(config, engine, fake_tg, monkeypatch) -> None:
    from app.container import Container

    monkeypatch.setattr(config, "bot_mode", "polling")
    c = Container(config, engine=engine, tg_transport=fake_tg.transport())
    await c.startup()
    import asyncio

    await asyncio.sleep(0.05)
    await c.shutdown()
    assert "deleteWebhook" in [m.method for m in fake_tg.calls]
    assert "setWebhook" not in [m.method for m in fake_tg.calls]


async def test_static_frontend_serving(config, container, tmp_path, monkeypatch) -> None:
    from app.main import create_app

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text("<html>client</html>")
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "index.html").write_text("<html>admin</html>")
    (tmp_path / "admin" / "main.js").write_text("console.log(1)")
    monkeypatch.setattr(config, "frontend_dir", str(tmp_path))
    app = create_app(config, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert "client" in (await client.get("/app/")).text
        assert "admin" in (await client.get("/admin/withdrawals")).text  # SPA fallback
        assert (await client.get("/admin/main.js")).status_code == 200
        assert (await client.get("/admin/missing.js")).status_code == 404
        assert (await client.get("/", follow_redirects=False)).headers["location"] == "/app/"
