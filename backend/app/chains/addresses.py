"""Address validation and tx hash normalization for TRC-20 and BEP-20 (pure)."""

from __future__ import annotations

import hashlib
import re

from Crypto.Hash import keccak

from app.domain.enums import Network

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {ch: i for i, ch in enumerate(B58_ALPHABET)}

TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
BSC_USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
TRON_ZERO_ADDRESS = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"  # base58check of 0x41 + 20 zero bytes
BSC_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class AddressError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = B58_ALPHABET[rem] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        if ch not in _B58_INDEX:
            raise ValueError(f"invalid base58 character {ch!r}")
        num = num * 58 + _B58_INDEX[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def tron_from_hex(value: str) -> str:
    """'41…' / '0x…' (20 bytes) / 21-byte hex -> base58check address."""
    raw = value.lower().removeprefix("0x")
    if len(raw) == 40:
        raw = "41" + raw
    if len(raw) != 42 or not raw.startswith("41"):
        raise ValueError(f"not a TRON hex address: {value!r}")
    payload = bytes.fromhex(raw)
    return b58encode(payload + _checksum(payload))


def tron_to_hex(address: str) -> str:
    """base58 -> 20-byte hex without the 0x41 prefix."""
    return b58decode(address)[1:21].hex()


def validate_trc20(address: str) -> str:
    addr = (address or "").strip()
    if len(addr) != 34 or not addr.startswith("T"):
        raise AddressError("Адрес TRC-20 должен состоять из 34 символов и начинаться с T.")
    try:
        raw = b58decode(addr)
    except ValueError:
        raise AddressError("Адрес TRC-20 содержит недопустимые символы.") from None
    if len(raw) != 25 or raw[0] != 0x41:
        raise AddressError("Некорректный адрес TRC-20.")
    if _checksum(raw[:21]) != raw[21:]:
        raise AddressError("Неверная контрольная сумма адреса TRC-20. Проверьте адрес.")
    return addr


def to_checksum_address(address: str) -> str:
    """EIP-55 mixed-case checksum encoding."""
    hex_addr = address.lower().removeprefix("0x")
    digest = keccak.new(digest_bits=256, data=hex_addr.encode()).hexdigest()
    return "0x" + "".join(ch.upper() if int(digest[i], 16) >= 8 else ch for i, ch in enumerate(hex_addr))


_HEX40 = re.compile(r"^0x[0-9a-fA-F]{40}$")


def validate_bep20(address: str) -> str:
    addr = (address or "").strip()
    if not _HEX40.fullmatch(addr):
        raise AddressError("Адрес BEP-20 должен начинаться с 0x и содержать 40 шестнадцатеричных символов.")
    body = addr[2:]
    checksummed = to_checksum_address(addr)
    if body != body.lower() and body != body.upper() and addr != checksummed:
        raise AddressError("Неверная контрольная сумма адреса BEP-20 (EIP-55). Проверьте адрес.")
    return checksummed


def validate_address(network: Network, address: str) -> str:
    """Validate and normalize a client's withdrawal address."""
    if network == Network.TRC20:
        addr = validate_trc20(address)
        forbidden = {TRON_USDT_CONTRACT: "контракта USDT", TRON_ZERO_ADDRESS: "нулевой адрес"}
    else:
        addr = validate_bep20(address)
        forbidden = {BSC_USDT_CONTRACT: "контракта USDT", BSC_ZERO_ADDRESS: "нулевой адрес"}
    if addr in forbidden:
        what = forbidden[addr]
        if what == "нулевой адрес":
            raise AddressError("Это нулевой адрес — на него нельзя выводить средства.")
        raise AddressError("Это адрес контракта USDT, а не кошелька. Укажите адрес своего кошелька.")
    return addr


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TxHashError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_tx_hash(network: Network, value: str) -> str:
    raw = (value or "").strip().lower()
    body = raw.removeprefix("0x")
    if not _HEX64.fullmatch(body):
        raise TxHashError("Хэш транзакции должен содержать 64 шестнадцатеричных символа.")
    return body if network == Network.TRC20 else "0x" + body


def same_address(network: Network, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    if network == Network.BEP20:
        return a.lower() == b.lower()
    return a == b
