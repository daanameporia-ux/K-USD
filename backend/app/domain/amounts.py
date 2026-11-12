"""Parsing and formatting of user-entered amounts (pure)."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, InvalidOperation

_SPACES = {" ", " ", " ", " ", "_"}
MAX_DIGITS = 12

MSG_RUB_INTEGER = "Сумма должна быть целым числом рублей, без копеек."
MSG_RUB_EMPTY = "Введите сумму числом, например 5000."
MSG_USDT_FORMAT = "Введите сумму числом, например 100 или 100.50."
MSG_USDT_PRECISION = "Укажите сумму с точностью не более 2 знаков после точки."
MSG_USDT_POSITIVE = "Сумма должна быть больше нуля."


class AmountError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def rub_range_message(min_rub: int, max_rub: int) -> str:
    return f"Сумма должна быть от {format_rub(min_rub)} до {format_rub(max_rub)} ₽."


def parse_rub(text: str, min_rub: int, max_rub: int) -> int:
    raw = (text or "").strip()
    if not raw:
        raise AmountError(MSG_RUB_EMPTY)
    if any(ch not in _SPACES and not ("0" <= ch <= "9") for ch in raw):
        raise AmountError(MSG_RUB_INTEGER)
    digits = "".join(ch for ch in raw if ch not in _SPACES)
    if not digits:
        raise AmountError(MSG_RUB_EMPTY)
    if len(digits.lstrip("0")) > MAX_DIGITS:
        raise AmountError(rub_range_message(min_rub, max_rub))
    value = int(digits)
    if value < min_rub or value > max_rub:
        raise AmountError(rub_range_message(min_rub, max_rub))
    return value


def parse_usdt(text: str) -> Decimal:
    raw = "".join(ch for ch in (text or "").strip() if ch not in _SPACES).replace(",", ".")
    if not raw or raw.count(".") > 1 or any(not (ch.isdigit() or ch == ".") for ch in raw):
        raise AmountError(MSG_USDT_FORMAT)
    if len(raw.split(".")[0].lstrip("0")) > MAX_DIGITS:
        raise AmountError(MSG_USDT_FORMAT)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise AmountError(MSG_USDT_FORMAT) from exc
    if value != value.quantize(Decimal("0.01"), rounding=ROUND_DOWN):
        raise AmountError(MSG_USDT_PRECISION)
    if value <= 0:
        raise AmountError(MSG_USDT_POSITIVE)
    return value.quantize(Decimal("0.01"))


def format_rub(value: int | Decimal) -> str:
    if isinstance(value, Decimal) and value != value.to_integral_value():
        whole = f"{value.quantize(Decimal('0.01')):,.2f}"
    else:
        whole = f"{int(value):,}"
    return whole.replace(",", " ")


def format_usdt(value: Decimal | None) -> str:
    if value is None:
        return "0.00"
    q = value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if q != value:
        # keep extra precision instead of hiding it (e.g. adjustments with 6 decimals)
        return f"{value.normalize():f}"
    return f"{q:f}"


def format_rate(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"
