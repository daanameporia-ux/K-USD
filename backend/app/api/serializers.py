"""JSON representations. Money and rates are strings (never floats)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.amounts import format_usdt
from app.domain.enums import VerifyStatus
from app.domain.pricing import Quote
from app.domain.verify import LABELS
from app.models import AntifraudEvent, AuditLog, LedgerEntry, Order, Staff, User, Withdrawal
from app.services import history, labels, stats


def verify_label(status: VerifyStatus | None) -> str | None:
    return LABELS[status] if status is not None else None


def money(value: Decimal | None) -> str | None:
    return None if value is None else format_usdt(value)


def num(value: Decimal | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    return f"{value.normalize():f}" if value == value.to_integral_value() else f"{value:f}".rstrip("0")


def rate_str(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.quantize(Decimal('0.01')):f}"


def ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def quote(q: Quote) -> dict[str, Any]:
    return {
        "amount_rub": str(q.amount_rub),
        "rate": rate_str(q.rate),
        "markup_pct": num(q.markup_pct),
        "fee_rub": num(q.margin_rub),
        "usdt_amount": money(q.usdt_amount),
        "effective_rate": rate_str(q.effective_rate),
    }


def order(o: Order, *, admin: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": o.id,
        "public_id": o.public_id,
        "status": o.status.value,
        "status_label": labels.ORDER_STATUS[o.status],
        "amount_rub": str(o.amount_rub),
        "rate": rate_str(o.rate),
        "markup_pct": num(o.markup_pct),
        "usdt_amount": money(o.usdt_amount),
        "credited_usdt": money(o.credited_usdt),
        "qr_url": o.qr_url,
        "created_at": ts(o.created_at),
        "expires_at": ts(o.expires_at),
        "paid_at": ts(o.paid_at),
        "finished_at": ts(o.finished_at),
    }
    if admin:
        data.update(
            {
                "user_id": o.user_id,
                "rate_source": o.rate_source,
                "margin_rub": num(o.margin_rub),
                "provider": o.provider,
                "provider_payment_id": o.provider_payment_id,
                "paid_amount_rub": None if o.paid_amount_rub is None else str(o.paid_amount_rub),
                "payer_info": o.payer_info,
                "review_reason": o.review_reason,
                "source": o.source.value,
            }
        )
    return data


def withdrawal(w: Withdrawal, *, admin: bool = False) -> dict[str, Any]:
    tx = w.tx_hash or (w.verify_details or {}).get("tx_hash")
    data: dict[str, Any] = {
        "id": w.id,
        "public_id": w.public_id,
        "status": w.status.value,
        "status_label": labels.WITHDRAWAL_STATUS[w.status],
        "network": w.network.value,
        "network_label": labels.NETWORK[w.network],
        "address": w.address,
        "amount": money(w.amount),
        "fee": money(w.fee),
        "amount_to_send": money(w.amount_to_send),
        "tx_hash": w.tx_hash,
        "explorer_url": history.explorer_url(w.network, w.tx_hash) if w.tx_hash else None,
        "reject_reason": w.reject_reason,
        "created_at": ts(w.created_at),
        "finished_at": ts(w.finished_at),
    }
    if admin:
        data.update(
            {
                "user_id": w.user_id,
                "verify_status": w.verify_status.value if w.verify_status else None,
                "verify_label": verify_label(w.verify_status),
                "verify_details": w.verify_details,
                "verify_attempts": w.verify_attempts,
                "last_verify_at": ts(w.last_verify_at),
                "last_tx_hash": tx,
                "last_explorer_url": history.explorer_url(w.network, tx) if tx else None,
                "forced": w.forced,
                "assigned_staff_id": w.assigned_staff_id,
                "completed_staff_id": w.completed_staff_id,
                "taken_at": ts(w.taken_at),
                "hash_submitted_at": ts(w.hash_submitted_at),
            }
        )
    return data


def history_item(item: history.HistoryItem) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "id": item.id,
        "public_id": item.public_id,
        "created_at": ts(item.created_at),
        "status": item.status,
        "status_label": item.status_label,
        "usdt": money(item.usdt),
        "amount_rub": None if item.amount_rub is None else str(item.amount_rub),
        "rate": rate_str(item.rate),
        "network": item.network.value if item.network else None,
        "address": item.address,
        "fee": money(item.fee),
        "amount_to_send": money(item.amount_to_send),
        "tx_hash": item.tx_hash,
        "explorer_url": item.explorer_url,
        "reject_reason": item.reject_reason,
    }


def user_admin(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "tg_id": u.tg_id,
        "tg_username": u.tg_username,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "status": u.status.value,
        "kyc_status": u.kyc_status.value,
        "markup_pct": num(u.markup_pct),
        "order_min_rub": u.order_min_rub,
        "order_max_rub": u.order_max_rub,
        "daily_turnover_limit_rub": u.daily_turnover_limit_rub,
        "balance": money(u.balance),
        "hold": money(u.hold),
        "can_create_orders": u.can_create_orders,
        "orders_block_reason": u.orders_block_reason,
        "is_suspicious": u.is_suspicious,
        "af_enabled": u.af_enabled,
        "af_reset_at": ts(u.af_reset_at),
        "bot_blocked": u.bot_blocked,
        "created_at": ts(u.created_at),
        "registered_at": ts(u.registered_at),
        "last_seen_at": ts(u.last_seen_at),
    }


def staff(s: Staff) -> dict[str, Any]:
    return {
        "id": s.id,
        "tg_id": s.tg_id,
        "tg_username": s.tg_username,
        "role": s.role.value,
        "is_active": s.is_active,
        "is_superadmin": s.is_superadmin,
        "login": s.login,
        "totp_enabled": s.totp_enabled,
        "invite_pending": s.tg_id is None,
        "invite_expires_at": ts(s.invite_expires_at),
        "created_at": ts(s.created_at),
        "last_login_at": ts(s.last_login_at),
    }


def ledger_entry(e: LedgerEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type.value,
        "balance_delta": num(e.balance_delta),
        "hold_delta": num(e.hold_delta),
        "balance_after": num(e.balance_after),
        "hold_after": num(e.hold_after),
        "order_id": e.order_id,
        "withdrawal_id": e.withdrawal_id,
        "staff_id": e.staff_id,
        "comment": e.comment,
        "created_at": ts(e.created_at),
    }


def af_event(e: AntifraudEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "rule": e.rule,
        "severity": e.severity.value,
        "details": e.details,
        "created_at": ts(e.created_at),
        "resolved_at": ts(e.resolved_at),
        "resolved_by": e.resolved_by,
    }


def audit_entry(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "staff_id": a.staff_id,
        "action": a.action,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "before": a.before,
        "after": a.after,
        "comment": a.comment,
        "ip": a.ip,
        "created_at": ts(a.created_at),
    }


def counts(c: stats.Counts) -> dict[str, Any]:
    return {
        "created": c.created,
        "paid": c.paid,
        "expired": c.expired,
        "cancelled": c.cancelled,
        "rejected": c.rejected,
        "review": c.review,
        "failed": c.failed,
        "pending": c.pending,
        "finished": c.finished,
        "conversion_pct": num(c.conversion_pct),
    }


def day_stats(d: stats.DayStats) -> dict[str, Any]:
    return {
        "day": d.day.isoformat(),
        **counts(d.counts),
        "turnover_rub": str(d.turnover_rub),
        "usdt": money(d.usdt),
        "margin_rub": num(d.margin_rub),
    }


def client_stats(s: stats.ClientStats) -> dict[str, Any]:
    return {
        "counts": counts(s.counts),
        "conversion_pct": num(s.conversion_pct),
        "window_size": s.window_size,
        "window_finished": s.window_finished,
        "window_conversion_pct": num(s.window_conversion_pct),
        "unpaid_streak": s.unpaid_streak,
        "turnover_rub": str(s.turnover_rub),
        "usdt_received": money(s.usdt_received),
        "margin_rub": num(s.margin_rub),
        "avg_check_rub": num(s.avg_check_rub),
        "median_pay_seconds": s.median_pay_seconds,
        "withdrawals_count": s.withdrawals_count,
        "withdrawals_sum": money(s.withdrawals_sum),
        "withdrawals_fees": money(s.withdrawals_fees),
        "first_deal_at": ts(s.first_deal_at),
        "last_deal_at": ts(s.last_deal_at),
    }


def period(p: stats.PeriodStats) -> dict[str, Any]:
    return {
        "start": ts(p.start),
        "end": ts(p.end),
        "orders": counts(p.orders),
        "conversion_pct": num(p.conversion_pct),
        "turnover_rub": str(p.turnover_rub),
        "usdt_issued": money(p.usdt_issued),
        "margin_rub": num(p.margin_rub),
        "new_clients": p.new_clients,
        "active_clients": p.active_clients,
        "withdrawals_completed": p.withdrawals_completed,
        "withdrawals_completed_sum": money(p.withdrawals_completed_sum),
        "fees_collected": money(p.fees_collected),
    }
