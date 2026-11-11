"""Russian labels for statuses."""

from __future__ import annotations

from app.domain.enums import Network, OrderStatus, WithdrawalStatus

ORDER_STATUS: dict[OrderStatus, str] = {
    OrderStatus.creating: "Создаётся",
    OrderStatus.pending: "Ожидает оплаты",
    OrderStatus.paid: "Оплачена",
    OrderStatus.expired: "Истекла",
    OrderStatus.cancelled: "Отменена",
    OrderStatus.failed: "Ошибка создания",
    OrderStatus.review: "На проверке",
    OrderStatus.rejected: "Отклонена",
}

WITHDRAWAL_STATUS: dict[WithdrawalStatus, str] = {
    WithdrawalStatus.pending: "В очереди",
    WithdrawalStatus.in_progress: "В работе",
    WithdrawalStatus.verifying: "Проверка в блокчейне",
    WithdrawalStatus.completed: "Исполнен",
    WithdrawalStatus.rejected: "Отклонён",
    WithdrawalStatus.cancelled: "Отменён",
}

NETWORK: dict[Network, str] = {Network.TRC20: "TRC-20", Network.BEP20: "BEP-20"}
