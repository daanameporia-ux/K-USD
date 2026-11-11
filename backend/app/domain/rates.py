"""Rapira response parser and jump guard (pure)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

SYMBOL = "USDT/RUB"
RATE_FIELDS = ("askPrice", "bidPrice", "close")


class RateParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedRate:
    symbol: str
    ask: Decimal | None
    bid: Decimal | None
    value: Decimal
    field: str
    raw: dict[str, Any]


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def parse_rapira(body: bytes | str, field: str = "askPrice") -> ParsedRate:
    """Strict on required fields, tolerant to extra ones. Numbers are parsed as Decimal, never float."""
    if field not in RATE_FIELDS:
        raise RateParseError(f"unsupported rate field {field!r}")
    try:
        doc = json.loads(body, parse_float=Decimal, parse_int=Decimal)
    except (ValueError, TypeError) as exc:
        raise RateParseError("response is not valid JSON") from exc
    if not isinstance(doc, dict):
        raise RateParseError("response is not a JSON object")
    if doc.get("code") != 0:
        raise RateParseError(f"code is {doc.get('code')!r}, expected 0")
    if doc.get("isWorking") != 1:
        raise RateParseError(f"isWorking is {doc.get('isWorking')!r}, expected 1")
    data = doc.get("data")
    if not isinstance(data, list):
        raise RateParseError("data is not a list")
    item = next((x for x in data if isinstance(x, dict) and x.get("symbol") == SYMBOL), None)
    if item is None:
        raise RateParseError(f"{SYMBOL} not found")
    value = _dec(item.get(field))
    if value is None:
        raise RateParseError(f"{field} is missing or not a number")
    if value <= 0:
        raise RateParseError(f"{field} is not positive: {value}")
    return ParsedRate(
        symbol=SYMBOL,
        ask=_dec(item.get("askPrice")),
        bid=_dec(item.get("bidPrice")),
        value=value,
        field=field,
        raw=_jsonable(item),
    )


def jump_pct(previous: Decimal, new: Decimal) -> Decimal:
    if previous <= 0:
        raise ValueError("previous rate must be positive")
    return abs(new - previous) / previous * 100


def is_jump(previous: Decimal | None, new: Decimal, guard_pct: Decimal) -> bool:
    if previous is None:
        return False
    return jump_pct(previous, new) > guard_pct
