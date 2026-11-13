from __future__ import annotations

import pytest

from app.domain.buyer import PROOF_MAX_BYTES, BuyerError, normalize_name, normalize_phone, sniff_proof


@pytest.mark.parametrize(
    "raw",
    [
        "+79161234567",
        "89161234567",
        "79161234567",
        "9161234567",
        "+7 (916) 123-45-67",
        "8 916 123 45 67",
        "+7.916.123.45.67",
    ],
)
def test_phone_is_normalized(raw: str) -> None:
    assert normalize_phone(raw) == "+79161234567"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "телефон",
        "+7916123456",  # too short
        "+791612345678",  # too long
        "+74951234567",  # a landline: SBP needs a mobile number
        "+380501234567",  # not Russian
        "+19161234567",
        "69161234567",
        "+7916123456a",
        "+7９１６１２３４５６７",  # non-ASCII digits
    ],
)
def test_phone_is_rejected(raw: str | None) -> None:
    with pytest.raises(BuyerError) as exc:
        normalize_phone(raw)
    assert exc.value.code == "invalid_buyer_phone" and "СБП" in exc.value.message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Иванов Иван Иванович", "Иванов Иван Иванович"),
        ("  Иванов   Иван\tИванович ", "Иванов Иван Иванович"),
        ("Римский-Корсаков Николай Андреевич", "Римский-Корсаков Николай Андреевич"),
        ("Ким Ян", "Ким Ян"),
        ("Ёлкин Пётр Семёнович", "Ёлкин Пётр Семёнович"),
        ("Mamedov Ali Vagif ogly", "Mamedov Ali Vagif ogly"),
    ],
)
def test_name_is_normalized(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "Иванов", "Иванов 2", "Иванов И.", "<b>Иван</b> Иванов", "а " * 7, "Я" * 121 + " Я"]
)
def test_name_is_rejected(raw: str | None) -> None:
    with pytest.raises(BuyerError) as exc:
        normalize_name(raw)
    assert exc.value.code == "invalid_buyer_name"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"\x00\x00\x00\x18ftypheic", "image/heic"),
        (b"%PDF-1.7", "application/pdf"),
    ],
)
def test_proof_type_is_detected_from_content(head: bytes, expected: str) -> None:
    assert sniff_proof(head + b"\x00" * 32) == expected


@pytest.mark.parametrize(
    "data", [b"", b"<html><script>alert(1)</script>", b"GIF89a", b"MZ\x90\x00", b"<svg xmlns='x'/>"]
)
def test_proof_of_unknown_type_is_rejected(data: bytes) -> None:
    with pytest.raises(BuyerError) as exc:
        sniff_proof(data)
    assert exc.value.code == "invalid_proof"


def test_proof_size_limit() -> None:
    assert sniff_proof(b"\x89PNG\r\n\x1a\n" + b"\x00" * (PROOF_MAX_BYTES - 8)) == "image/png"
    with pytest.raises(BuyerError) as exc:
        sniff_proof(b"\x89PNG\r\n\x1a\n" + b"\x00" * (PROOF_MAX_BYTES - 7))
    assert exc.value.code == "proof_too_large"
