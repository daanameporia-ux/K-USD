"""Texts of client notifications produced by services (sent via the outbox)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain.amounts import format_rub, format_usdt
from app.domain.timefmt import fmt_time


def order_caption(public_id: str, amount_rub: int, usdt: Decimal, expires_at: datetime) -> str:
    return (
        f"🧾 Заявка <b>{public_id}</b>\n\n"
        f"Сумма к оплате: <b>{format_rub(amount_rub)} ₽</b>\n"
        f"Будет зачислено: <b>{format_usdt(usdt)} USDT</b>\n"
        f"Оплатить до: <b>{fmt_time(expires_at)}</b> (МСК)\n\n"
        "Отсканируйте QR-код в приложении банка или нажмите «Оплатить». "
        "Оплатите ровно указанную сумму одним платежом."
    )


def order_paid(public_id: str, credited: Decimal, balance: Decimal) -> str:
    return (
        f"✅ Оплата по заявке <b>{public_id}</b> получена.\n"
        f"Зачислено: <b>{format_usdt(credited)} USDT</b>\n"
        f"Баланс: <b>{format_usdt(balance)} USDT</b>"
    )


def order_expired(public_id: str) -> str:
    return f"⌛ Заявка <b>{public_id}</b> истекла. Если нужно, создайте новую."


def order_cancelled(public_id: str) -> str:
    return f"Заявка <b>{public_id}</b> отменена."


def order_review(public_id: str) -> str:
    return (
        f"ℹ️ Платёж по заявке <b>{public_id}</b> получен, но требует проверки. "
        "Решение примет оператор, мы сообщим результат."
    )


def order_rejected(public_id: str, comment: str) -> str:
    return f"❌ Заявка <b>{public_id}</b> отклонена после проверки.\nПричина: {comment}"


def caption_closed(public_id: str, status_text: str) -> str:
    return f"🧾 Заявка <b>{public_id}</b>\n\n{status_text}"
