from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.errors import ServiceError
from app.models import AuditLog, Setting
from app.services import settings as settings_svc


async def test_defaults(sessionmaker) -> None:
    async with sessionmaker() as s:
        biz = await settings_svc.load(s)
    assert biz.order_min_rub == 1000
    assert biz.order_max_rub == 100000
    assert biz.default_markup_pct == Decimal("5.00")
    assert biz.manual_rate is None
    assert biz.cooldown_steps == [(2, 5), (3, 15), (4, 60)]
    assert biz.b9_enabled is False and biz.b2_enabled is True


async def test_update_writes_audit(sessionmaker, clock) -> None:
    async with sessionmaker.begin() as s:
        biz, changes = await settings_svc.update(
            s,
            {"order_min_rub": 2000, "manual_rate": "95.5", "purchases_enabled": True},
            staff_id=None,
            now=clock.now(),
            ip="1.2.3.4",
        )
    assert biz.order_min_rub == 2000
    assert {c.key for c in changes} == {"order_min_rub", "manual_rate"}
    async with sessionmaker() as s:
        assert (await settings_svc.load(s)).manual_rate == Decimal("95.5")
        audits = (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    assert [(a.entity_id, a.before, a.after) for a in audits] == [
        ("order_min_rub", {"value": 1000}, {"value": 2000}),
        ("manual_rate", {"value": None}, {"value": "95.5"}),
    ]
    assert audits[0].ip == "1.2.3.4"


@pytest.mark.parametrize(
    "changes",
    [
        {"order_min_rub": 0},
        {"order_min_rub": 200000},
        {"default_markup_pct": "-1"},
        {"rate_field": "foo"},
        {"b3_cooldowns": "abc"},
        {"b3_cooldowns": ""},
        {"no_such_key": 1},
    ],
)
async def test_update_validation(sessionmaker, clock, changes) -> None:
    async with sessionmaker.begin() as s:
        with pytest.raises(ServiceError):
            await settings_svc.update(s, changes, staff_id=None, now=clock.now())


async def test_broken_stored_value_falls_back(sessionmaker) -> None:
    async with sessionmaker.begin() as s:
        s.add(Setting(key="order_min_rub", value="garbage"))
        s.add(Setting(key="order_max_rub", value=50000))
    async with sessionmaker() as s:
        biz = await settings_svc.load(s)
    assert biz.order_min_rub == 1000
    assert biz.order_max_rub == 50000
    assert settings_svc.dump(biz)["default_markup_pct"] == "5.00"
