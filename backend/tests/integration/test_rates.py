from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from app.domain.errors import ServiceError
from app.jobs.rate import refresh_rate
from app.models import NotificationOutbox, Rate, Staff
from app.services import rates
from app.services import settings as settings_svc

FIXTURE = (Path(__file__).parent.parent / "fixtures" / "rapira_rates.json").read_bytes()
URL = "https://api.rapira.net/open/market/rates"


def rapira(ask: str) -> bytes:
    doc = json.loads(FIXTURE)
    for item in doc["data"]:
        if item["symbol"] == "USDT/RUB":
            item["askPrice"] = float(ask)
    return json.dumps(doc).encode()


async def alerts(container) -> list[str]:
    async with container.db() as s:
        rows = (await s.execute(select(NotificationOutbox).order_by(NotificationOutbox.id))).scalars().all()
    return [r.payload["params"]["text"] for r in rows if r.chat_id == 0]


async def current(container):
    async with container.db() as s:
        biz = await settings_svc.load(s)
        return await rates.current(s, biz, container.clock.now())


@respx.mock
async def test_refresh_and_current(container, clock) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=FIXTURE))
    await refresh_rate(container)
    cur = await current(container)
    assert cur is not None and cur.value == Decimal("86.38") and cur.source == "rapira" and cur.age_seconds == 0
    clock.advance(seconds=180)
    assert (await current(container)).age_seconds == 180
    clock.advance(seconds=1)
    assert await current(container) is None
    async with container.db() as s:
        biz = await settings_svc.load(s)
        with pytest.raises(rates.RateUnavailable):
            await rates.require(s, biz, clock.now())
    assert await alerts(container) == []


@respx.mock
async def test_no_rate_alert_throttled(container, clock) -> None:
    respx.get(URL).mock(return_value=httpx.Response(502, text="bad gateway"))
    await refresh_rate(container)
    await refresh_rate(container)
    assert await alerts(container) == ["❗ Нет валидного курса — покупки недоступны."]
    clock.advance(minutes=10)
    await refresh_rate(container)
    assert len(await alerts(container)) == 2


@respx.mock
async def test_network_error_and_parse_error(container, clock) -> None:
    route = respx.get(URL)
    route.mock(side_effect=httpx.ConnectTimeout("timeout"))
    await refresh_rate(container)
    route.mock(return_value=httpx.Response(200, content=b'{"code": 0, "isWorking": 1, "data": []}'))
    await refresh_rate(container)
    texts = await alerts(container)
    assert any("не разобран" in t for t in texts)
    async with container.db() as s:
        assert (await s.execute(select(Rate))).scalars().all() == []


@respx.mock
async def test_jump_guard_and_accept(container, clock) -> None:
    route = respx.get(URL)
    route.mock(return_value=httpx.Response(200, content=rapira("100")))
    await refresh_rate(container)
    clock.advance(seconds=45)
    route.mock(return_value=httpx.Response(200, content=rapira("103.5")))
    await refresh_rate(container)
    assert (await current(container)).value == Decimal("100")
    clock.advance(seconds=45)
    await refresh_rate(container)  # still jumped: no second alert within 10 minutes
    jump_alerts = [t for t in await alerts(container) if "Скачок" in t]
    assert len(jump_alerts) == 1 and "3.50%" in jump_alerts[0]

    async with container.db.begin() as s:
        staff = Staff(role="admin", tg_id=1)
        s.add(staff)
        await s.flush()
        row = await rates.accept_jump(s, staff_id=staff.id, now=clock.now())
        assert row.accepted
    assert (await current(container)).value == Decimal("103.5")
    async with container.db.begin() as s:
        with pytest.raises(ServiceError):
            await rates.accept_jump(s, staff_id=staff.id, now=clock.now())
    # small move after accepting is applied normally
    clock.advance(seconds=45)
    route.mock(return_value=httpx.Response(200, content=rapira("104")))
    await refresh_rate(container)
    assert (await current(container)).value == Decimal("104")


@respx.mock
async def test_accept_jump_too_old(container, clock) -> None:
    route = respx.get(URL)
    route.mock(return_value=httpx.Response(200, content=rapira("100")))
    await refresh_rate(container)
    route.mock(return_value=httpx.Response(200, content=rapira("120")))
    await refresh_rate(container)
    clock.advance(seconds=200)
    async with container.db.begin() as s:
        staff = Staff(role="admin", tg_id=1)
        s.add(staff)
        await s.flush()
        with pytest.raises(ServiceError) as exc:
            await rates.accept_jump(s, staff_id=staff.id, now=clock.now())
    assert exc.value.code == "jump_too_old"


async def test_manual_rate(container, clock) -> None:
    async with container.db.begin() as s:
        staff = Staff(role="admin", tg_id=1)
        s.add(staff)
        await s.flush()
        await rates.set_manual(s, Decimal("95.10"), staff_id=staff.id, now=clock.now())
    cur = await current(container)
    assert cur.value == Decimal("95.10") and cur.source == "manual"
    async with container.db.begin() as s:
        await rates.set_manual(s, None, staff_id=staff.id, now=clock.now())
    assert await current(container) is None
    texts = await alerts(container)
    assert texts[0].startswith("✋ Ручной курс установлен") and texts[1].startswith("Ручной курс снят")


@respx.mock
async def test_rate_field_setting(container, clock) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=FIXTURE))
    async with container.db.begin() as s:
        await settings_svc.update(s, {"rate_field": "bidPrice"}, staff_id=None, now=clock.now())
    await refresh_rate(container)
    assert (await current(container)).value == Decimal("86.37")


@respx.mock
async def test_cleanup_keeps_latest(container, clock) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=FIXTURE))
    await refresh_rate(container)
    clock.advance(days=31)
    async with container.db.begin() as s:
        assert await rates.cleanup(s, clock.now() - timedelta(days=30)) == 0
        s.add(
            Rate(
                source="rapira",
                symbol="USDT/RUB",
                value=Decimal(1),
                accepted=False,
                raw={},
                fetched_at=clock.now() - timedelta(days=40),
            )
        )
    async with container.db.begin() as s:
        assert await rates.cleanup(s, clock.now() - timedelta(days=30)) == 1
