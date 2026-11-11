from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Network, VerifyStatus, WithdrawalStatus
from app.models.base import USDT, Base, pg_enum


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_withdrawals_user_idempotency"),
        Index(
            "uq_withdrawals_network_tx_hash",
            "network",
            "tx_hash",
            unique=True,
            postgresql_where=text("tx_hash IS NOT NULL"),
        ),
        Index("ix_withdrawals_status_created", "status", "created_at"),
        Index("ix_withdrawals_user_created", "user_id", text("created_at DESC")),
        Index("ix_withdrawals_network_address", "network", "address"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    network: Mapped[Network] = mapped_column(pg_enum(Network, "network"), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    fee: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    amount_to_send: Mapped[Decimal] = mapped_column(USDT, nullable=False)
    status: Mapped[WithdrawalStatus] = mapped_column(
        pg_enum(WithdrawalStatus, "withdrawal_status"), nullable=False
    )
    tx_hash: Mapped[str | None] = mapped_column(Text)
    verify_status: Mapped[VerifyStatus | None] = mapped_column(pg_enum(VerifyStatus, "verify_status"))
    verify_details: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, default=dict, server_default=text("'{}'")
    )
    verify_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_verify_at: Mapped[datetime | None]
    hash_submitted_at: Mapped[datetime | None]
    forced: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    assigned_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    completed_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    taken_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
