"""Business settings stored in the `settings` table and edited from the admin panel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ServiceError
from app.models import Setting
from app.services import audit

Pct = Decimal


def parse_cooldowns(value: str) -> list[tuple[int, int]]:
    """'2:5, 3:15, 4:60' -> [(2, 5), (3, 15), (4, 60)] sorted by streak."""
    steps: list[tuple[int, int]] = []
    for raw in value.split(","):
        part = raw.strip()
        if not part:
            continue
        streak, _, minutes = part.partition(":")
        steps.append((int(streak), int(minutes)))
    steps.sort()
    return steps


class BizSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # orders
    order_min_rub: int = Field(1000, ge=1)
    order_max_rub: int = Field(100000, ge=1)
    order_ttl_minutes: int = Field(15, ge=1, le=120)
    default_markup_pct: Decimal = Field(Decimal("5.00"), ge=0, lt=100, decimal_places=2)
    purchases_enabled: bool = True
    # withdrawals
    withdraw_min_usdt: Decimal = Field(Decimal("50"), gt=0)
    withdraw_fee_trc20: Decimal = Field(Decimal("4"), ge=0)
    withdraw_fee_bep20: Decimal = Field(Decimal("2"), ge=0)
    withdraw_admin_only_from_usdt: Decimal = Field(Decimal("5000"), gt=0)
    withdrawals_enabled: bool = True
    # rate
    rate_ttl_seconds: int = Field(45, ge=5, le=3600)
    rate_max_age_seconds: int = Field(180, ge=10)
    rate_jump_guard_pct: Decimal = Field(Decimal("3"), gt=0)
    rate_field: Literal["askPrice", "bidPrice", "close"] = "askPrice"
    manual_rate: Decimal | None = Field(None, gt=0)
    # misc
    support_contact: str = Field("@support", min_length=1, max_length=200)
    # antifraud — main rules
    af_enabled: bool = True
    af_window_orders: int = Field(30, ge=1)
    af_min_orders: int = Field(10, ge=1)
    af_min_conversion_pct: Decimal = Field(Decimal("80"), ge=0, le=100)
    af_streak_suspicious: int = Field(3, ge=1)
    af_streak_block: int = Field(5, ge=1)
    # B2 rate limit
    b2_enabled: bool = True
    b2_per_hour: int = Field(5, ge=1)
    b2_per_day: int = Field(15, ge=1)
    # B3 growing cooldown
    b3_enabled: bool = True
    b3_cooldowns: str = "2:5, 3:15, 4:60"
    # B4 shared withdrawal address
    b4_enabled: bool = True
    # B5 blacklist
    b5_enabled: bool = True
    # B6 large withdrawals admin only
    b6_enabled: bool = True
    # B8 global conversion drop
    b8_enabled: bool = True
    b8_window_minutes: int = Field(60, ge=5)
    b8_min_orders: int = Field(20, ge=1)
    b8_min_conversion_pct: Decimal = Field(Decimal("40"), ge=0, le=100)
    b8_auto_pause: bool = False
    # B9 newcomer limits
    b9_enabled: bool = False
    b9_paid_orders_threshold: int = Field(3, ge=1)
    b9_max_order_rub: int = Field(20000, ge=1)
    b9_max_daily_rub: int = Field(50000, ge=1)
    # B10 daily turnover per client
    b10_enabled: bool = False
    b10_daily_rub: int = Field(300000, ge=1)
    # B11 withdrawal hold after purchase
    b11_enabled: bool = False
    b11_hold_hours: int = Field(24, ge=1)
    # B12 fast transit
    b12_enabled: bool = False
    b12_window_minutes: int = Field(10, ge=1)
    b12_balance_pct: Decimal = Field(Decimal("90"), gt=0, le=100)
    # B13 many payers
    b13_enabled: bool = False
    b13_max_payers: int = Field(3, ge=1)
    b13_window_days: int = Field(30, ge=1)
    # B14 daily withdrawal limit
    b14_enabled: bool = False
    b14_daily_usdt: Decimal = Field(Decimal("10000"), gt=0)

    @field_validator("b3_cooldowns")
    @classmethod
    def _check_cooldowns(cls, v: str) -> str:
        try:
            steps = parse_cooldowns(v)
        except ValueError as exc:
            raise ValueError("формат: «2:5, 3:15, 4:60» (неоплат подряд : минут паузы)") from exc
        if not steps or any(s < 1 or m < 1 for s, m in steps):
            raise ValueError("нужно хотя бы одно правило вида «2:5»")
        return v

    @model_validator(mode="after")
    def _check_ranges(self) -> BizSettings:
        if self.order_min_rub > self.order_max_rub:
            raise ValueError("order_min_rub не может быть больше order_max_rub")
        if self.af_streak_suspicious > self.af_streak_block:
            raise ValueError("af_streak_suspicious не может быть больше af_streak_block")
        return self

    @property
    def cooldown_steps(self) -> list[tuple[int, int]]:
        return parse_cooldowns(self.b3_cooldowns)


DEFAULTS = BizSettings()
KEYS: tuple[str, ...] = tuple(BizSettings.model_fields.keys())


def _to_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


async def load(session: AsyncSession) -> BizSettings:
    rows = (await session.execute(select(Setting.key, Setting.value))).all()
    stored = {key: value for key, value in rows if key in BizSettings.model_fields}
    try:
        return BizSettings.model_validate({**DEFAULTS.model_dump(), **stored})
    except ValidationError:
        # A broken individual value must not take the service down: fall back key by key.
        merged = DEFAULTS.model_dump()
        for key, value in stored.items():
            candidate = {**merged, key: value}
            try:
                BizSettings.model_validate(candidate)
            except ValidationError:
                continue
            merged = candidate
        return BizSettings.model_validate(merged)


@dataclass(frozen=True)
class SettingChange:
    key: str
    before: Any
    after: Any


async def update(
    session: AsyncSession,
    changes: dict[str, Any],
    *,
    staff_id: int | None,
    now: datetime,
    ip: str | None = None,
    comment: str | None = None,
) -> tuple[BizSettings, list[SettingChange]]:
    unknown = sorted(set(changes) - set(KEYS))
    if unknown:
        raise ServiceError("unknown_setting", f"Неизвестные настройки: {', '.join(unknown)}")
    current = await load(session)
    try:
        updated = BizSettings.model_validate({**current.model_dump(), **changes})
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "settings"
        raise ServiceError("invalid_setting", f"Некорректное значение {loc}: {first['msg']}") from exc
    applied: list[SettingChange] = []
    for key in changes:
        before = getattr(current, key)
        after = getattr(updated, key)
        if before == after:
            continue
        value = _to_json(after)
        stmt = insert(Setting).values(key=key, value=value, updated_at=now, updated_by=staff_id)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Setting.key], set_={"value": value, "updated_at": now, "updated_by": staff_id}
        )
        await session.execute(stmt)
        await audit.write(
            session,
            action="settings.update",
            staff_id=staff_id,
            entity_type="setting",
            entity_id=key,
            before={"value": _to_json(before)},
            after={"value": value},
            ip=ip,
            comment=comment,
        )
        applied.append(SettingChange(key, before, after))
    return updated, applied


def dump(settings: BizSettings) -> dict[str, Any]:
    return {key: _to_json(getattr(settings, key)) for key in KEYS}
