from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.chains.addresses import (
    BSC_USDT_CONTRACT,
    TRON_USDT_CONTRACT,
    AddressError,
    TxHashError,
    b58decode,
    b58encode,
    normalize_tx_hash,
    same_address,
    to_checksum_address,
    tron_from_hex,
    tron_to_hex,
    validate_address,
)
from app.chains.base import NOT_FOUND, Transfer, TransferInfo, pick_transfer
from app.domain.enums import Network, VerifyStatus
from app.domain.verify import evaluate

TRON_OK = "TGfb53j2wY8J2F7J5RQhs6gdKpC2j6zsBY"
BSC_OK = "0xB5c3809c0263244Eeb1948F223B0FF3bBEd695Ea"


def test_base58_roundtrip() -> None:
    for data in [b"", b"\0\0abc", bytes(range(30))]:
        assert b58decode(b58encode(data)) == data
    with pytest.raises(ValueError):
        b58decode("0OIl")


def test_tron_hex_conversions() -> None:
    assert tron_from_hex("41a614f803b6fd780986a42c78ec9c7f77e6ded13c") == TRON_USDT_CONTRACT
    assert tron_from_hex("0xa614f803b6fd780986a42c78ec9c7f77e6ded13c") == TRON_USDT_CONTRACT
    assert tron_from_hex("A614F803B6FD780986A42C78EC9C7F77E6DED13C") == TRON_USDT_CONTRACT
    assert tron_to_hex(TRON_USDT_CONTRACT) == "a614f803b6fd780986a42c78ec9c7f77e6ded13c"
    with pytest.raises(ValueError):
        tron_from_hex("42" + "00" * 20)


def test_trc20_valid() -> None:
    assert validate_address(Network.TRC20, f"  {TRON_OK} ") == TRON_OK


@pytest.mark.parametrize(
    "addr",
    [
        TRON_OK[:-1],  # 33 chars
        "X" + TRON_OK[1:],  # wrong prefix
        TRON_OK[:-1] + ("C" if TRON_OK[-1] != "C" else "D"),  # checksum broken
        TRON_OK[:5] + "0" + TRON_OK[6:],  # invalid base58 char
        "1" * 34,
        "T" + "1" * 33,
        "",
    ],
)
def test_trc20_invalid(addr) -> None:
    with pytest.raises(AddressError):
        validate_address(Network.TRC20, addr)


def test_trc20_contract_and_zero_rejected() -> None:
    with pytest.raises(AddressError) as exc:
        validate_address(Network.TRC20, TRON_USDT_CONTRACT)
    assert "контракта USDT" in exc.value.message
    with pytest.raises(AddressError) as exc:
        validate_address(Network.TRC20, tron_from_hex("00" * 20))
    assert "нулевой" in exc.value.message


def test_bep20_valid_forms() -> None:
    assert validate_address(Network.BEP20, BSC_OK) == BSC_OK
    assert validate_address(Network.BEP20, BSC_OK.lower()) == BSC_OK
    assert validate_address(Network.BEP20, "0x" + BSC_OK[2:].upper()) == BSC_OK
    assert (
        to_checksum_address("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed")
        == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    )


@pytest.mark.parametrize(
    "addr",
    [
        "0xB5c3809c0263244eeb1948F223B0FF3bBEd695Ea",  # wrong EIP-55 checksum (mixed case)
        BSC_OK[:-1],
        BSC_OK + "0",
        "B5c3809c0263244Eeb1948F223B0FF3bBEd695Ea00",
        "0x" + "g" * 40,
        "",
    ],
)
def test_bep20_invalid(addr) -> None:
    with pytest.raises(AddressError):
        validate_address(Network.BEP20, addr)


def test_bep20_contract_and_zero_rejected() -> None:
    for addr in (BSC_USDT_CONTRACT, BSC_USDT_CONTRACT.lower(), "0x" + "0" * 40):
        with pytest.raises(AddressError):
            validate_address(Network.BEP20, addr)


def test_hash_normalization() -> None:
    h = "AB" * 32
    assert normalize_tx_hash(Network.TRC20, f" 0x{h} ") == "ab" * 32
    assert normalize_tx_hash(Network.TRC20, h) == "ab" * 32
    assert normalize_tx_hash(Network.BEP20, h) == "0x" + "ab" * 32
    assert normalize_tx_hash(Network.BEP20, "0X" + h) == "0x" + "ab" * 32
    for bad in ["ab" * 31, "zz" * 32, "", "0x" + "ab" * 33]:
        with pytest.raises(TxHashError):
            normalize_tx_hash(Network.TRC20, bad)
        with pytest.raises(TxHashError):
            normalize_tx_hash(Network.BEP20, bad)


def test_same_address() -> None:
    assert same_address(Network.BEP20, BSC_OK, BSC_OK.lower())
    assert not same_address(Network.TRC20, TRON_OK, TRON_OK.lower())
    assert not same_address(Network.TRC20, None, TRON_OK)


def test_pick_transfer() -> None:
    a = Transfer("C", "X", Decimal(1))
    b = Transfer("USDT", "Y", Decimal(2))
    c = Transfer("USDT", "Z", Decimal(3))
    assert pick_transfer([a, b, c], "USDT", "Z", case_insensitive=False) == c
    assert pick_transfer([a, b], "USDT", "Z", case_insensitive=False) == b
    assert pick_transfer([a], "usdt", "z", case_insensitive=True) == a
    assert pick_transfer([], "USDT", "Z", case_insensitive=False) is None


NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def info(**kw) -> TransferInfo:
    base = dict(
        found=True,
        success=True,
        confirmations=20,
        token_contract="USDT",
        to_address="TO",
        amount=Decimal("10"),
        block_time=NOW,
    )
    return TransferInfo(**{**base, **kw})


@pytest.mark.parametrize(
    ("transfer", "expected"),
    [
        (info(), VerifyStatus.ok),
        (NOT_FOUND, VerifyStatus.not_found),
        (info(success=False), VerifyStatus.tx_failed),
        (info(token_contract="OTHER"), VerifyStatus.wrong_token),
        (info(token_contract=None), VerifyStatus.wrong_token),
        (info(to_address="ELSE"), VerifyStatus.wrong_recipient),
        (info(amount=Decimal("10.000001")), VerifyStatus.wrong_amount),
        (info(amount=None), VerifyStatus.wrong_amount),
        (info(block_time=NOW - timedelta(seconds=1)), VerifyStatus.too_old),
        (info(confirmations=14), VerifyStatus.pending_confirmations),
    ],
)
def test_evaluate(transfer, expected) -> None:
    status = evaluate(
        transfer,
        usdt_contract="USDT",
        expected_to="TO",
        expected_amount=Decimal(10),
        not_before=NOW,
        required_confirmations=15,
        same_address=lambda a, b: a == b,
    )
    assert status == expected
