"""Exchange rate: background refresh from Rapira, manual override, jump guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ServiceError
from app.domain.rates import ParsedRate, is_jump, jump_pct
from app.models import Rate
from app.services import audit, notify
from app.services import settings as settings_svc
from app.services.settings import BizSettings

RATE_UNAVAILABLE_TEXT = "Покупка временно недоступна, попробуйте через несколько минут."


class RateUnavailable(ServiceError):
    def __init__(self) -> None:
        super().__init__("rate_unavailable", RATE_UNAVAILABLE_TEXT, status=503)


@dataclass(frozen=True)
class CurrentRate:
    value: Decimal
    source: Literal["rapira", "manual"]
    fetched_at: datetime | None
    age_seconds: int | None


async def latest_accepted(session: AsyncSession) -> Rate | None:
    stmt = select(Rate).where(Rate.accepted.is_(True)).order_by(Rate.fetched_at.desc(), Rate.id.desc()).limit(1)
    result: Rate | None = (await session.execute(stmt)).scalar_one_or_none()
    return result


async def latest(session: AsyncSession) -> Rate | None:
    stmt = select(Rate).order_by(Rate.fetched_at.desc(), Rate.id.desc()).limit(1)
    result: Rate | None = (await session.execute(stmt)).scalar_one_or_none()
    return result


async def current(session: AsyncSession, biz: BizSettings, now: datetime) -> CurrentRate | None:
    if biz.manual_rate is not None:
        return CurrentRate(biz.manual_rate, "manual", None, None)
    row = await latest_accepted(session)
    if row is None:
        return None
    age = int((now - row.fetched_at).total_seconds())
    if age > biz.rate_max_age_seconds:
        return None
    return CurrentRate(row.value, "rapira", row.fetched_at, max(age, 0))


async def require(session: AsyncSession, biz: BizSettings, now: datetime) -> CurrentRate:
    rate = await current(session, biz, now)
    if rate is None:
        raise RateUnavailable()
    return rate


@dataclass(frozen=True)
class StoreResult:
    rate: Rate
    jump_pct: Decimal | None


async def store(
    session: AsyncSession, parsed: ParsedRate, *, now: datetime, guard_pct: Decimal, source: str = "rapira"
) -> StoreResult:
    previous = await latest_accepted(session)
    prev_value = previous.value if previous is not None else None
    jumped = is_jump(prev_value, parsed.value, guard_pct)
    row = Rate(
        source=source,
        symbol=parsed.symbol,
        ask=parsed.ask,
        bid=parsed.bid,
        value=parsed.value,
        accepted=not jumped,
        reject_reason="jump" if jumped else None,
        raw=parsed.raw,
        fetched_at=now,
    )
    session.add(row)
    await session.flush()
    pct = jump_pct(prev_value, parsed.value) if prev_value is not None else None
    return StoreResult(row, pct)


async def accept_jump(session: AsyncSession, *, staff_id: int, now: datetime, ip: str | None = None) -> Rate:
    biz = await settings_svc.load(session)
    row = await latest(session)
    if row is None or row.accepted or row.reject_reason != "jump":
        raise ServiceError("no_pending_jump", "Нет курса, ожидающего подтверждения.")
    if (now - row.fetched_at).total_seconds() > biz.rate_max_age_seconds:
        raise ServiceError("jump_too_old", "Курс устарел, дождитесь следующего обновления.")
    previous = await latest_accepted(session)
    row.accepted = True
    row.reject_reason = None
    await audit.write(
        session,
        action="rate.accept_jump",
        staff_id=staff_id,
        entity_type="rate",
        entity_id=row.id,
        before={"value": str(previous.value) if previous else None},
        after={"value": str(row.value)},
        ip=ip,
    )
    await notify.alert(session, f"✅ Скачок курса подтверждён вручную: {row.value}", now=now)
    return row


async def set_manual(
    session: AsyncSession, value: Decimal | None, *, staff_id: int, now: datetime, ip: str | None = None
) -> BizSettings:
    biz, changes = await settings_svc.update(
        session, {"manual_rate": None if value is None else str(value)}, staff_id=staff_id, now=now, ip=ip
    )
    if changes:
        text = f"✋ Ручной курс установлен: {value}" if value is not None else "Ручной курс снят, используется Rapira"
        await notify.alert(session, text, now=now)
    return biz


async def cleanup(session: AsyncSession, older_than: datetime) -> int:
    keep = await latest_accepted(session)
    stmt = delete(Rate).where(Rate.fetched_at < older_than)
    if keep is not None:
        stmt = stmt.where(Rate.id != keep.id)
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)
