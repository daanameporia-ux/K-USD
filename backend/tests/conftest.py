from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/kusd_test")

os.environ.update(
    {
        "ENV": "test",
        "BOT_TOKEN": "123456:TEST-TOKEN-abcdefghijklmnopqrstuvwxyz",
        "BOT_MODE": "webhook",
        "PUBLIC_BASE_URL": "https://example.test",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret-0123456789",
        "DATABASE_URL": TEST_DB,
        "SESSION_SECRET": "s" * 40,
        "SUPERADMIN_TG_IDS": "1000",
        "STAFF_CHAT_ID": "-100500",
        "PAYMENT_PROVIDER": "mock",
        "MOCK_PROVIDER_SECRET": "mock-secret-0123456789",
        "TELEGRAM_API_URL": "https://tg.fake",
        "RUN_SCHEDULER": "false",
    }
)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker  # noqa: E402

from app.config import Config, load_config  # noqa: E402
from app.db import make_engine, make_sessionmaker  # noqa: E402
from app.models import Base  # noqa: E402


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config as AlembicConfig

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(base, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(base, "alembic"))
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DB

    async def _reset() -> None:
        engine = make_engine(load_config().database_url, pool_size=1)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset())
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def config() -> Config:
    return load_config()


@pytest.fixture(scope="session")
def _migrated() -> None:
    _migrate()


@pytest.fixture(scope="session")
async def engine(_migrated: None, config: Config) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(config.database_url, pool_size=10)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@pytest.fixture(autouse=True)
async def clean_db(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    if "engine" not in request.fixturenames and "container" not in request.fixturenames:
        yield
        return
    engine: AsyncEngine = request.getfixturevalue("engine")
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


# --- container & fakes ---------------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

from app.clock import FakeClock  # noqa: E402
from tests.fakes import FakeTelegram, UpdateFactory  # noqa: E402


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 11, 9, 0, tzinfo=UTC))


@pytest.fixture
def fake_tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def tg_sleep() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def updates() -> UpdateFactory:
    return UpdateFactory()


@pytest.fixture
def container_kwargs() -> dict:
    return {}


@pytest.fixture
async def container(config, engine, fake_tg, clock, tg_sleep, container_kwargs):
    from app.container import Container

    c = Container(
        config, engine=engine, tg_transport=fake_tg.transport(), clock=clock, tg_sleep=tg_sleep, **container_kwargs
    )
    yield c
    await c.aclose()


class BotDriver:
    """Drives the bot through the dispatcher like Telegram would."""

    def __init__(self, container, fake_tg: FakeTelegram, factory: UpdateFactory) -> None:
        self.c = container
        self.tg = fake_tg
        self.f = factory

    async def send(self, tg_id: int, text: str, **kw) -> None:
        await self.c.dispatcher.process(self.f.message(tg_id, text, **kw))

    async def click(self, tg_id: int, data: str, **kw) -> None:
        await self.c.dispatcher.process(self.f.callback(tg_id, data, **kw))

    def last(self, tg_id: int) -> str:
        return self.tg.last_text(tg_id)

    def buttons(self, tg_id: int) -> list[dict]:
        markup = self.tg.last_markup(tg_id) or {}
        rows = markup.get("inline_keyboard") or markup.get("keyboard") or []
        return [b for row in rows for b in row]

    def callback_data(self, tg_id: int) -> list[str]:
        return [b["callback_data"] for b in self.buttons(tg_id) if "callback_data" in b]

    async def register(self, tg_id: int, username: str | None = None) -> None:
        await self.send(tg_id, "/start", username=username)
        await self.click(tg_id, "reg:start", username=username)


@pytest.fixture
def bot(container, fake_tg, updates) -> BotDriver:
    return BotDriver(container, fake_tg, updates)
