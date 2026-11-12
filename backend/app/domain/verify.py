"""Decision on a blockchain transfer vs. the expected withdrawal (pure)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.domain.enums import VerifyStatus


class TransferLike(Protocol):
    @property
    def found(self) -> bool: ...
    @property
    def success(self) -> bool: ...
    @property
    def confirmations(self) -> int: ...
    @property
    def token_contract(self) -> str | None: ...
    @property
    def to_address(self) -> str | None: ...
    @property
    def amount(self) -> Decimal | None: ...
    @property
    def block_time(self) -> datetime | None: ...


def evaluate(
    info: TransferLike,
    *,
    usdt_contract: str,
    expected_to: str,
    expected_amount: Decimal,
    not_before: datetime,
    required_confirmations: int,
    same_address: Callable[[str | None, str | None], bool],
) -> VerifyStatus:
    if not info.found:
        return VerifyStatus.not_found
    if not info.success:
        return VerifyStatus.tx_failed
    if info.token_contract is None or not same_address(info.token_contract, usdt_contract):
        return VerifyStatus.wrong_token
    if not same_address(info.to_address, expected_to):
        return VerifyStatus.wrong_recipient
    if info.amount is None or info.amount != expected_amount:
        return VerifyStatus.wrong_amount
    if info.block_time is None or info.block_time < not_before:
        return VerifyStatus.too_old
    if info.confirmations < required_confirmations:
        return VerifyStatus.pending_confirmations
    return VerifyStatus.ok


FINAL_FAILURES = frozenset(
    {
        VerifyStatus.tx_failed,
        VerifyStatus.wrong_token,
        VerifyStatus.wrong_recipient,
        VerifyStatus.wrong_amount,
        VerifyStatus.too_old,
    }
)
RETRY = frozenset({VerifyStatus.not_found, VerifyStatus.pending_confirmations, VerifyStatus.explorer_unavailable})

LABELS: dict[VerifyStatus, str] = {
    VerifyStatus.ok: "Проверка пройдена",
    VerifyStatus.not_found: "Транзакция не найдена",
    VerifyStatus.pending_confirmations: "Недостаточно подтверждений",
    VerifyStatus.tx_failed: "Транзакция неуспешна",
    VerifyStatus.wrong_token: "Нет перевода USDT по нужному контракту",
    VerifyStatus.wrong_recipient: "Получатель не совпадает",
    VerifyStatus.wrong_amount: "Сумма не совпадает",
    VerifyStatus.too_old: "Транзакция раньше создания вывода",
    VerifyStatus.duplicate_hash: "Хэш уже использован",
    VerifyStatus.explorer_unavailable: "Эксплорер недоступен",
}
