"""Status machines for orders and withdrawals (single transition table each)."""

from __future__ import annotations

from app.domain.enums import OrderStatus, WithdrawalStatus
from app.domain.errors import InvalidTransition

OS = OrderStatus
WS = WithdrawalStatus

ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OS.creating: frozenset({OS.pending, OS.failed}),
    OS.pending: frozenset({OS.paid, OS.expired, OS.cancelled, OS.review}),
    OS.expired: frozenset({OS.review}),
    OS.cancelled: frozenset({OS.review}),
    OS.review: frozenset({OS.paid, OS.rejected}),
    OS.paid: frozenset(),
    OS.failed: frozenset(),
    OS.rejected: frozenset(),
}

WITHDRAWAL_TRANSITIONS: dict[WithdrawalStatus, frozenset[WithdrawalStatus]] = {
    WS.pending: frozenset({WS.in_progress, WS.cancelled, WS.rejected}),
    WS.in_progress: frozenset({WS.verifying, WS.rejected, WS.pending}),
    WS.verifying: frozenset({WS.completed, WS.in_progress}),
    WS.completed: frozenset(),
    WS.rejected: frozenset(),
    WS.cancelled: frozenset(),
}


def order_transition(current: OrderStatus, target: OrderStatus) -> OrderStatus:
    if target not in ORDER_TRANSITIONS[current]:
        raise InvalidTransition("order", current.value, target.value)
    return target


def withdrawal_transition(current: WithdrawalStatus, target: WithdrawalStatus) -> WithdrawalStatus:
    if target not in WITHDRAWAL_TRANSITIONS[current]:
        raise InvalidTransition("withdrawal", current.value, target.value)
    return target
