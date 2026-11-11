from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.amounts import (
    MSG_RUB_INTEGER,
    MSG_USDT_PRECISION,
    AmountError,
    format_rub,
    format_usdt,
    parse_rub,
    parse_usdt,
    rub_range_message,
)
from app.domain.pricing import quote


def test_control_example() -> None:
    q = quote(1000, Decimal("5"), Decimal("100"))
    assert q.usdt_amount == Decimal("9.50")
    assert q.margin_rub == Decimal("50.00")
    assert q.effective_rate == Decimal("105.27")


def test_rounding_is_always_down() -> None:
    # 1000 * 0.95 / 86.38 = 10.99791...
    assert quote(1000, Decimal("5"), Decimal("86.38")).usdt_amount == Decimal("10.99")
    # 1000 / 3 = 333.333.. -> 333.33 ; and 2000/3 = 666.666.. -> 666.66 (not .67)
    assert quote(2000, Decimal("0"), Decimal("3")).usdt_amount == Decimal("666.66")


def test_zero_and_fractional_markup() -> None:
    assert quote(1000, Decimal("0"), Decimal("100")).usdt_amount == Decimal("10.00")
    assert quote(1000, Decimal("0"), Decimal("100")).margin_rub == Decimal("0.00")
    q = quote(1000, Decimal("2.75"), Decimal("100"))
    assert q.usdt_amount == Decimal("9.72")
    assert q.margin_rub == Decimal("27.50")


@pytest.mark.parametrize("rate", [Decimal(0), Decimal(-1)])
def test_invalid_rate(rate) -> None:
    with pytest.raises(ValueError):
        quote(1000, Decimal(5), rate)


def test_invalid_markup_and_amount() -> None:
    with pytest.raises(ValueError):
        quote(1000, Decimal(100), Decimal(90))
    with pytest.raises(ValueError):
        quote(-1, Decimal(1), Decimal(90))


@given(
    amount=st.integers(min_value=0, max_value=10_000_000),
    markup=st.decimals(min_value=0, max_value=Decimal("99.99"), places=2),
    rate=st.decimals(min_value=Decimal("0.000001"), max_value=Decimal(1_000_000), places=6),
)
def test_property_never_overpays(amount, markup, rate) -> None:
    q = quote(amount, markup, rate)
    assert q.usdt_amount >= 0
    assert q.usdt_amount * rate <= Decimal(amount) * (1 - markup / 100)
    # and we never round down by a full cent or more
    assert (q.usdt_amount + Decimal("0.01")) * rate > Decimal(amount) * (1 - markup / 100)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1000", 1000), ("10 000", 10000), ("10 000", 10000), (" 5000 ", 5000), ("100000", 100000)],
)
def test_parse_rub_ok(text, expected) -> None:
    assert parse_rub(text, 1000, 100000) == expected


@pytest.mark.parametrize("text", ["1000.50", "1000,5", "abc", "-100", "10к", "1e3", "+1000"])
def test_parse_rub_not_integer(text) -> None:
    with pytest.raises(AmountError) as exc:
        parse_rub(text, 1000, 100000)
    assert exc.value.message == MSG_RUB_INTEGER


@pytest.mark.parametrize("text", ["0", "999", "100001", "9" * 40, "000"])
def test_parse_rub_out_of_range(text) -> None:
    with pytest.raises(AmountError) as exc:
        parse_rub(text, 1000, 100000)
    assert exc.value.message == rub_range_message(1000, 100000)


@pytest.mark.parametrize("text", ["", "   "])
def test_parse_rub_empty(text) -> None:
    with pytest.raises(AmountError):
        parse_rub(text, 1000, 100000)


def test_parse_usdt() -> None:
    assert parse_usdt("100") == Decimal("100.00")
    assert parse_usdt("100,5") == Decimal("100.50")
    assert parse_usdt("1 000.25") == Decimal("1000.25")
    with pytest.raises(AmountError) as exc:
        parse_usdt("1.234")
    assert exc.value.message == MSG_USDT_PRECISION
    for bad in ["", "abc", "1.2.3", "-5", "0", "0.00", "1" * 30]:
        with pytest.raises(AmountError):
            parse_usdt(bad)


def test_formatting() -> None:
    assert format_rub(100000) == "100 000"
    assert format_rub(Decimal("50.00")) == "50"
    assert format_rub(Decimal("27.50")) == "27.50"
    assert format_usdt(Decimal("9.5")) == "9.50"
    assert format_usdt(Decimal("9.123456")) == "9.123456"
    assert format_usdt(None) == "0.00"
