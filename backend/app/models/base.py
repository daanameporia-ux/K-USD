from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, MetaData, Numeric
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

USDT = Numeric(20, 6)
RATE = Numeric(20, 6)
PCT = Numeric(6, 2)


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        Decimal: USDT,
        dict[str, Any]: JSONB,
    }
