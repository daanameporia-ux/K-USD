"""Buyer details collected with every purchase order: SBP phone, full name, payment screenshot."""

from __future__ import annotations

import re

PROOF_MAX_BYTES = 10 * 1024 * 1024
PROOF_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "application/pdf": "pdf",
}

MSG_PHONE = (
    "Укажите номер телефона покупателя в российском формате: +7 9XX XXX-XX-XX. "
    "Номер должен быть привязан к СБП (Системе быстрых платежей) в банке покупателя."
)
MSG_NAME = "Укажите ФИО покупателя полностью: фамилия, имя и отчество."
MSG_PROOF_TYPE = "Подойдёт фото или файл: JPG, PNG, WEBP, HEIC или PDF."
MSG_PROOF_SIZE = "Файл слишком большой. Максимум 10 МБ."

_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:[-'’][A-Za-zА-Яа-яЁё]+)*\Z")
_PHONE_JUNK = re.compile(r"[\s()\-.]")


class BuyerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_phone(raw: str | None) -> str:
    """Russian mobile number → `+79XXXXXXXXX`. SBP works only with Russian mobile numbers."""
    text = _PHONE_JUNK.sub("", raw or "")
    if text.startswith("+7"):
        digits = text[2:]
    elif len(text) == 11 and text[0] in "78":
        digits = text[1:]
    else:
        digits = text
    if len(digits) != 10 or not digits.isascii() or not digits.isdigit() or digits[0] != "9":
        raise BuyerError("invalid_buyer_phone", MSG_PHONE)
    return "+7" + digits


def normalize_name(raw: str | None) -> str:
    """Full name: at least surname and first name, letters only; the patronymic is expected when there is one."""
    words = (raw or "").split()
    text = " ".join(words)
    if not 2 <= len(words) <= 6 or len(text) > 120 or not all(_WORD.match(w) for w in words):
        raise BuyerError("invalid_buyer_name", MSG_NAME)
    return text


def sniff_proof(data: bytes) -> str:
    """Content type by magic bytes; the declared type and the file name are never trusted."""
    if len(data) > PROOF_MAX_BYTES:
        raise BuyerError("proof_too_large", MSG_PROOF_SIZE)
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heim", b"heis"):
        return "image/heic"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    raise BuyerError("invalid_proof", MSG_PROOF_TYPE)
