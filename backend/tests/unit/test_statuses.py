from __future__ import annotations

import itertools

import pytest

from app.domain.enums import OrderStatus, WithdrawalStatus
from app.domain.errors import InvalidTransition
from app.domain.statuses import ORDER_TRANSITIONS, WITHDRAWAL_TRANSITIONS, order_transition, withdrawal_transition

OS, WS = OrderStatus, WithdrawalStatus

ALLOWED_ORDER = {
    (OS.creating, OS.pending),
    (OS.creating, OS.failed),
    (OS.pending, OS.paid),
    (OS.pending, OS.expired),
    (OS.pending, OS.cancelled),
    (OS.pending, OS.review),
    (OS.expired, OS.review),
    (OS.cancelled, OS.review),
    (OS.review, OS.paid),
    (OS.review, OS.rejected),
}
ALLOWED_WD = {
    (WS.pending, WS.in_progress),
    (WS.pending, WS.cancelled),
    (WS.pending, WS.rejected),
    (WS.in_progress, WS.verifying),
    (WS.in_progress, WS.rejected),
    (WS.in_progress, WS.pending),
    (WS.verifying, WS.completed),
    (WS.verifying, WS.in_progress),
}


def test_tables_match_spec() -> None:
    assert {(a, b) for a, targets in ORDER_TRANSITIONS.items() for b in targets} == ALLOWED_ORDER
    assert {(a, b) for a, targets in WITHDRAWAL_TRANSITIONS.items() for b in targets} == ALLOWED_WD


@pytest.mark.parametrize(("a", "b"), list(itertools.product(OrderStatus, OrderStatus)))
def test_order_transitions(a, b) -> None:
    if (a, b) in ALLOWED_ORDER:
        assert order_transition(a, b) == b
    else:
        with pytest.raises(InvalidTransition) as exc:
            order_transition(a, b)
        assert exc.value.details == {"entity": "order", "from": a.value, "to": b.value}


@pytest.mark.parametrize(("a", "b"), list(itertools.product(WithdrawalStatus, WithdrawalStatus)))
def test_withdrawal_transitions(a, b) -> None:
    if (a, b) in ALLOWED_WD:
        assert withdrawal_transition(a, b) == b
    else:
        with pytest.raises(InvalidTransition):
            withdrawal_transition(a, b)
