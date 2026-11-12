from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import OrderSource, OrderStatus
from app.models.base import PCT, RATE, USDT, Base, pg_enum


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_orders_provider_payment"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),
        Index("ix_orders_user_created", "user_id", text("created_at DESC")),
        Index("ix_orders_status_expires", "status", "expires_at"),
        Index("ix_orders_finished_at", "finished_at"),
        Index(
            "uq_orders_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('creating', 'pending')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    rate_source: Mapped[str] = mapped_column(Text, nullable=False)
    markup_pct: Mapped[Decimal] = mapped_column(PCT, nullable=False)
    usdt_amount: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    margin_rub: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    credited_usdt: Mapped[Decimal | None] = mapped_column(USDT)
    status: Mapped[OrderStatus] = mapped_column(pg_enum(OrderStatus, "order_status"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    qr_url: Mapped[str | None] = mapped_column(Text)
    paid_amount_rub: Mapped[int | None] = mapped_column(Integer)
    payer_info: Mapped[dict[str, Any] | None]
    review_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[OrderSource] = mapped_column(pg_enum(OrderSource, "order_source"), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    paid_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
