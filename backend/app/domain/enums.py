"""Enumerations shared by domain logic, models and API."""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    registering = "registering"
    active = "active"
    blocked = "blocked"


class KycStatus(StrEnum):
    none = "none"
    passed = "passed"
    rejected = "rejected"


class OrderStatus(StrEnum):
    creating = "creating"
    pending = "pending"
    paid = "paid"
    expired = "expired"
    cancelled = "cancelled"
    failed = "failed"
    review = "review"
    rejected = "rejected"


FINISHED_ORDER_STATUSES = frozenset(
    {OrderStatus.paid, OrderStatus.expired, OrderStatus.cancelled, OrderStatus.rejected}
)
ACTIVE_ORDER_STATUSES = frozenset({OrderStatus.creating, OrderStatus.pending})


class OrderSource(StrEnum):
    bot = "bot"
    webapp = "webapp"


class Network(StrEnum):
    TRC20 = "TRC20"
    BEP20 = "BEP20"


class WithdrawalStatus(StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    verifying = "verifying"
    completed = "completed"
    rejected = "rejected"
    cancelled = "cancelled"


class VerifyStatus(StrEnum):
    ok = "ok"
    not_found = "not_found"
    pending_confirmations = "pending_confirmations"
    tx_failed = "tx_failed"
    wrong_token = "wrong_token"
    wrong_recipient = "wrong_recipient"
    wrong_amount = "wrong_amount"
    too_old = "too_old"
    duplicate_hash = "duplicate_hash"
    explorer_unavailable = "explorer_unavailable"


class LedgerType(StrEnum):
    deposit = "deposit"
    withdrawal_hold = "withdrawal_hold"
    withdrawal_release = "withdrawal_release"
    withdrawal_complete = "withdrawal_complete"
    adjustment = "adjustment"


class StaffRole(StrEnum):
    admin = "admin"
    operator = "operator"


class SubjectType(StrEnum):
    user = "user"
    staff = "staff"


class AuthMethod(StrEnum):
    telegram = "telegram"
    password = "password"


class AfSeverity(StrEnum):
    info = "info"
    suspicious = "suspicious"
    block = "block"
