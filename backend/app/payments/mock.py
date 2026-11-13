"""Mock payment provider: NSPK-like links, HMAC-signed callbacks, state in `mock_payments`."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.models import MockPayment
from app.payments.base import CallbackEvent, CallbackParseError, QrPayment

SIGNATURE_HEADER = "X-Signature"


class MockProvider:
    name = "mock"

    def __init__(self, secret: str, db: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._secret = secret.encode()
        self._db = db
        self._clock = clock
        self.fail_next_create: Exception | None = None  # test hook: simulate provider outage
        self.last_create: dict[str, Any] | None = None

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
        if self.fail_next_create is not None:
            exc, self.fail_next_create = self.fail_next_create, None
            raise exc
        payment_id = "MOCK" + uuid.uuid4().hex.upper()
        expires_at = self._clock.now() + timedelta(seconds=ttl_seconds)
        url = f"https://qr.nspk.ru/{payment_id}?type=02&bank=100000000000&sum={amount_rub * 100}&cur=RUB&crc=0000"
        async with self._db.begin() as session:
            session.add(
                MockPayment(
                    provider_payment_id=payment_id,
                    order_public_id=order_public_id,
                    amount_rub=amount_rub,
                    status="pending",
                    expires_at=expires_at,
                    created_at=self._clock.now(),
                )
            )
        raw = {
            "payment_id": payment_id,
            "description": description,
            "ttl_seconds": ttl_seconds,
            "buyer_phone": buyer_phone,
            "buyer_name": buyer_name,
        }
        self.last_create = raw  # test hook: what the provider was given
        return QrPayment(provider_payment_id=payment_id, qr_url=url, expires_at=expires_at, raw=raw)

    async def get_status(self, provider_payment_id: str) -> CallbackEvent | None:
        async with self._db() as session:
            row = (
                await session.execute(select(MockPayment).where(MockPayment.provider_payment_id == provider_payment_id))
            ).scalar_one_or_none()
        if row is None or row.status not in ("paid", "failed", "expired"):
            return None
        return CallbackEvent(
            event_key=f"mock-status:{provider_payment_id}:{row.status}",
            provider_payment_id=provider_payment_id,
            status=row.status,  # type: ignore[arg-type]
            amount_rub=row.paid_amount_rub,
            payer_info=None,
            raw={"source": "get_status", "status": row.status},
        )

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def verify_callback(self, headers: Mapping[str, str], body: bytes) -> bool:
        lowered = {k.lower(): v for k, v in headers.items()}
        got = lowered.get(SIGNATURE_HEADER.lower(), "")
        return bool(got) and hmac.compare_digest(got, self.sign(body))

    def parse_callback(self, body: bytes) -> CallbackEvent:
        try:
            doc = json.loads(body)
            status = doc["status"]
            if status not in ("paid", "failed", "expired"):
                raise CallbackParseError(f"unknown status {status!r}")
            amount = doc.get("amount_rub")
            if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool) or amount < 0):
                raise CallbackParseError("amount_rub must be a non-negative integer")
            payer = doc.get("payer")
            if payer is not None and not isinstance(payer, dict):
                raise CallbackParseError("payer must be an object")
            return CallbackEvent(
                event_key=str(doc["event_id"]),
                provider_payment_id=str(doc["payment_id"]),
                status=status,
                amount_rub=amount,
                payer_info=payer,
                raw=doc,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise CallbackParseError(str(exc)) from exc

    async def mark(self, provider_payment_id: str, status: str, paid_amount_rub: int | None) -> None:
        async with self._db.begin() as session:
            row = await session.get(MockPayment, provider_payment_id)
            if row is not None:
                row.status = status
                row.paid_amount_rub = paid_amount_rub

    def build_callback(
        self, provider_payment_id: str, status: str, amount_rub: int | None, payer: dict[str, Any] | None = None
    ) -> tuple[dict[str, str], bytes]:
        doc: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "payment_id": provider_payment_id,
            "status": status,
            "amount_rub": amount_rub,
        }
        if payer is not None:
            doc["payer"] = payer
        body = json.dumps(doc).encode()
        return {SIGNATURE_HEADER: self.sign(body), "Content-Type": "application/json"}, body
