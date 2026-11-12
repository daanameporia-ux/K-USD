from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import LedgerType
from app.models.base import USDT, Base, pg_enum


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index(
            "uq_ledger_deposit_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("type = 'deposit'"),
        ),
        Index("ix_ledger_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[LedgerType] = mapped_column(pg_enum(LedgerType, "ledger_type"), nullable=False)
    balance_delta: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    hold_delta: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    hold_after: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    withdrawal_id: Mapped[int | None] = mapped_column(ForeignKey("withdrawals.id"))
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
