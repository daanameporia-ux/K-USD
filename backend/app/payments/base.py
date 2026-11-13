"""Payment provider extension point. See docs/PAYMENT_PROVIDER.md."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class QrPayment:
    provider_payment_id: str
    qr_url: str  # NSPK link: https://qr.nspk.ru/...
    expires_at: datetime
    raw: dict[str, Any]


@dataclass(frozen=True)
class CallbackEvent:
    event_key: str  # unique event key for idempotency
    provider_payment_id: str
    status: Literal["paid", "failed", "expired"]
    amount_rub: int | None
    payer_info: dict[str, Any] | None
    raw: dict[str, Any]


class CallbackParseError(ValueError):
    pass


class PaymentProvider(Protocol):
    name: str

    async def create_qr(
        self,
        *,
        order_public_id: str,
        amount_rub: int,
        ttl_seconds: int,
        description: str,
        buyer_phone: str | None = None,
        buyer_name: str | None = None,
    ) -> QrPayment:
        """`buyer_phone` (+79XXXXXXXXX, linked to SBP) and `buyer_name` (full name) are what the client entered
        for this order; None when the requirement is switched off for the client."""
        ...

    async def get_status(self, provider_payment_id: str) -> CallbackEvent | None: ...

    def verify_callback(self, headers: Mapping[str, str], body: bytes) -> bool: ...

    def parse_callback(self, body: bytes) -> CallbackEvent: ...
