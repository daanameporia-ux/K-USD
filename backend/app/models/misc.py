from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AfSeverity, Network
from app.models.base import RATE, Base, pg_enum


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_staff", "staff_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None]
    after: Mapped[dict[str, Any] | None]
    comment: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class AntifraudEvent(Base):
    __tablename__ = "antifraud_events"
    __table_args__ = (Index("ix_af_events_user_rule", "user_id", "rule", "resolved_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[AfSeverity] = mapped_column(pg_enum(AfSeverity, "af_severity"), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))


class PaymentCallback(Base):
    __tablename__ = "payment_callbacks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str | None] = mapped_column(Text, unique=True)
    headers: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    signature_valid: Mapped[bool | None]
    processed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class MockPayment(Base):
    __tablename__ = "mock_payments"

    provider_payment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_public_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    paid_amount_rub: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Rate(Base):
    __tablename__ = "rates"
    __table_args__ = (Index("ix_rates_fetched", "fetched_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    ask: Mapped[Decimal | None] = mapped_column(RATE)
    bid: Mapped[Decimal | None] = mapped_column(RATE)
    value: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    accepted: Mapped[bool] = mapped_column(nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(nullable=False)


class TgUpdate(Base):
    __tablename__ = "tg_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    received_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class FsmState(Base):
    __tablename__ = "fsm_states"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    state: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class AddressBlacklist(Base):
    __tablename__ = "address_blacklist"
    __table_args__ = (UniqueConstraint("network", "address", name="uq_blacklist_network_address"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    network: Mapped[Network] = mapped_column(pg_enum(Network, "network"), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class SavedAddress(Base):
    """The client's address book for withdrawals."""

    __tablename__ = "saved_addresses"
    __table_args__ = (UniqueConstraint("user_id", "network", "address", name="uq_saved_addresses_user_address"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    network: Mapped[Network] = mapped_column(pg_enum(Network, "network"), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class NotificationOutbox(Base):
    __tablename__ = "notifications_outbox"
    __table_args__ = (
        Index("ix_outbox_due", "next_attempt_at", postgresql_where=text("sent_at IS NULL AND failed = false")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(nullable=False)
    sent_at: Mapped[datetime | None]
    failed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
