from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import KycStatus, UserStatus
from app.models.base import PCT, USDT, Base, pg_enum


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="balance_nonneg"),
        CheckConstraint("hold >= 0", name="hold_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    tg_username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"), nullable=False, default=UserStatus.registering
    )
    kyc_status: Mapped[KycStatus] = mapped_column(
        pg_enum(KycStatus, "kyc_status"), nullable=False, default=KycStatus.none
    )
    kyc_data: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, default=dict, server_default=text("'{}'")
    )
    markup_pct: Mapped[Decimal] = mapped_column(PCT, nullable=False)
    order_min_rub: Mapped[int | None] = mapped_column(Integer)
    order_max_rub: Mapped[int | None] = mapped_column(Integer)
    daily_turnover_limit_rub: Mapped[int | None] = mapped_column(Integer)
    balance: Mapped[Decimal] = mapped_column(USDT, nullable=False, default=Decimal(0), server_default="0")
    hold: Mapped[Decimal] = mapped_column(USDT, nullable=False, default=Decimal(0), server_default="0")
    can_create_orders: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    orders_block_reason: Mapped[str | None] = mapped_column(Text)
    is_suspicious: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    af_enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    af_reset_at: Mapped[datetime | None]
    bot_blocked: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    registered_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
