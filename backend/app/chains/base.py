"""Blockchain verification interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class ChainUnavailable(Exception):
    """The explorer / RPC could not be reached or returned garbage."""


@dataclass(frozen=True)
class TransferInfo:
    found: bool
    success: bool
    confirmations: int
    token_contract: str | None
    to_address: str | None
    amount: Decimal | None  # already divided by token decimals
    block_time: datetime | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


NOT_FOUND = TransferInfo(False, False, 0, None, None, None, None)


@dataclass(frozen=True)
class Transfer:
    contract: str
    to_address: str
    amount: Decimal


def pick_transfer(
    transfers: list[Transfer], usdt_contract: str, expected_to: str, *, case_insensitive: bool
) -> Transfer | None:
    """Prefer a USDT transfer to the expected recipient, then any USDT transfer, then anything."""

    def eq(a: str, b: str) -> bool:
        return a.lower() == b.lower() if case_insensitive else a == b

    usdt = [t for t in transfers if eq(t.contract, usdt_contract)]
    for t in usdt:
        if eq(t.to_address, expected_to):
            return t
    if usdt:
        return usdt[0]
    return transfers[0] if transfers else None


class ChainVerifier(Protocol):
    usdt_contract: str
    required_confirmations: int

    async def get_usdt_transfer(self, tx_hash: str, expected_to: str) -> TransferInfo: ...

    def explorer_url(self, tx_hash: str) -> str: ...
