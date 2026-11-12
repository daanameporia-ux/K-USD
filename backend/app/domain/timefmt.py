from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def to_msk(dt: datetime) -> datetime:
    return dt.astimezone(MSK)


def fmt_dt(dt: datetime | None) -> str:
    return "—" if dt is None else to_msk(dt).strftime("%d.%m.%Y %H:%M")


def fmt_time(dt: datetime) -> str:
    return to_msk(dt).strftime("%H:%M")


def msk_date(dt: datetime) -> date:
    return to_msk(dt).date()
