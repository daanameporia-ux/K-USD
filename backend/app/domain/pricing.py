"""Purchase price calculation (pure, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

USDT_QUANT = Decimal("0.01")
RUB_QUANT = Decimal("0.01")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Quote:
    amount_rub: int
    rate: Decimal
    markup_pct: Decimal
    usdt_amount: Decimal
    margin_rub: Decimal
    effective_rate: Decimal


def quote(amount_rub: int, markup_pct: Decimal, rate: Decimal) -> Quote:
    """usdt = ROUND_DOWN(amount × (1 − markup/100) / rate, 2); margin = amount × markup / 100.

    The markup is a commission taken from the RUB amount. `effective_rate` is how many RUB the client
    effectively pays for 1 USDT (rounded up, display only).
    """
    if rate <= 0:
        raise ValueError("rate must be positive")
    if amount_rub < 0:
        raise ValueError("amount must be non-negative")
    if not (Decimal(0) <= markup_pct < HUNDRED):
        raise ValueError("markup must be in [0, 100)")
    amount = Decimal(amount_rub)
    keep = 1 - markup_pct / HUNDRED
    usdt = (amount * keep / rate).quantize(USDT_QUANT, rounding=ROUND_DOWN)
    margin = (amount * markup_pct / HUNDRED).quantize(RUB_QUANT, rounding=ROUND_HALF_UP)
    effective = (rate / keep).quantize(Decimal("0.01"), rounding=ROUND_UP)
    return Quote(amount_rub, rate, markup_pct, usdt, margin, effective)
