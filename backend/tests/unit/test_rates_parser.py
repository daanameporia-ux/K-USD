from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.rates import RateParseError, is_jump, jump_pct, parse_rapira

FIXTURE = (Path(__file__).parent.parent / "fixtures" / "rapira_rates.json").read_bytes()


def body(items, **top) -> bytes:
    doc = {"data": items, "code": 0, "message": "SUCCESS", "isWorking": 1, **top}
    return json.dumps(doc).encode()


def test_live_fixture() -> None:
    parsed = parse_rapira(FIXTURE)
    assert parsed.symbol == "USDT/RUB"
    assert parsed.value == Decimal("86.38")
    assert parsed.ask == Decimal("86.38")
    assert parsed.bid == Decimal("86.37")
    assert parsed.raw["baseCurrency"] == "RUB"
    assert parse_rapira(FIXTURE, "bidPrice").value == Decimal("86.37")
    assert parse_rapira(FIXTURE, "close").value == Decimal("86.37")


def test_precision_not_lost() -> None:
    raw = b'{"data":[{"symbol":"USDT/RUB","askPrice":86.123456789012345678}],"code":0,"isWorking":1}'
    assert parse_rapira(raw).value == Decimal("86.123456789012345678")


def test_extra_fields_tolerated_and_symbol_searched() -> None:
    items = [{"symbol": "BTC/USDT", "askPrice": 1}, {"symbol": "USDT/RUB", "askPrice": 90.5, "new": [1, 2]}]
    assert parse_rapira(body(items, extra={"a": 1})).value == Decimal("90.5")


@pytest.mark.parametrize(
    "raw",
    [
        body([{"symbol": "BTC/USDT", "askPrice": 1}]),
        body([{"symbol": "USDT/RUB", "askPrice": 0}]),
        body([{"symbol": "USDT/RUB", "askPrice": -1}]),
        body([{"symbol": "USDT/RUB"}]),
        body([{"symbol": "USDT/RUB", "askPrice": "abc"}]),
        body([{"symbol": "USDT/RUB", "askPrice": True}]),
        body([{"symbol": "USDT/RUB", "askPrice": 90}], code=1),
        body([{"symbol": "USDT/RUB", "askPrice": 90}], isWorking=0),
        b'{"data": {}, "code": 0, "isWorking": 1}',
        b"<html>502</html>",
        b"[]",
        b"",
    ],
)
def test_invalid_responses(raw) -> None:
    with pytest.raises(RateParseError):
        parse_rapira(raw)


def test_unsupported_field() -> None:
    with pytest.raises(RateParseError):
        parse_rapira(FIXTURE, "open")


def test_jump() -> None:
    assert jump_pct(Decimal(100), Decimal(103)) == Decimal(3)
    assert not is_jump(Decimal(100), Decimal(103), Decimal(3))
    assert is_jump(Decimal(100), Decimal("103.01"), Decimal(3))
    assert is_jump(Decimal(100), Decimal("96.9"), Decimal(3))
    assert not is_jump(None, Decimal(1), Decimal(3))
    with pytest.raises(ValueError):
        jump_pct(Decimal(0), Decimal(1))
