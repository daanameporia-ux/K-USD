"""Withdrawals: creation with hold, operator queue, tx hash verification."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.addresses import AddressError, TxHashError, normalize_tx_hash, same_address, validate_address
from app.chains.base import ChainUnavailable, ChainVerifier
from app.domain.amounts import format_usdt
from app.domain.enums import LedgerType, Network, VerifyStatus, WithdrawalStatus
from app.domain.errors import Conflict, Forbidden, NotFound, ServiceError
from app.domain.statuses import withdrawal_transition
from app.domain.verify import FINAL_FAILURES, LABELS, evaluate
from app.logging import get_logger
from app.models import AddressBlacklist, LedgerEntry, User, Withdrawal
from app.services import antifraud, audit, ledger, notify
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.services.actor import Actor
from app.services.labels import NETWORK
from app.services.settings import BizSettings

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)

NOT_FOUND_TIMEOUT = timedelta(minutes=30)
RECHECK_EVERY = timedelta(seconds=30)
VERIFY_TIMEOUT = 45.0
CENT = Decimal("0.01")

MSG_DISABLED = "Вывод временно приостановлен. Попробуйте позже."


def fee_for(biz: BizSettings, network: Network) -> Decimal:
    return biz.withdraw_fee_trc20 if network == Network.TRC20 else biz.withdraw_fee_bep20


async def available_for_withdrawal(session: AsyncSession, user: User, biz: BizSettings, now: datetime) -> Decimal:
    """Balance minus deposits younger than B11 hold hours (if enabled)."""
    available = user.balance
    if biz.b11_enabled:
        fresh = (
            await session.execute(
                select(func.coalesce(func.sum(LedgerEntry.balance_delta), 0)).where(
                    LedgerEntry.user_id == user.id,
                    LedgerEntry.type == LedgerType.deposit,
                    LedgerEntry.created_at > now - timedelta(hours=biz.b11_hold_hours),
                )
            )
        ).scalar_one()
        available -= Decimal(fresh)
    return max(available, Decimal(0))


@dataclass(frozen=True)
class WithdrawConfig:
    fees: dict[Network, Decimal]
    min_amount: Decimal
    available: Decimal
    balance: Decimal
    enabled: bool


async def get_config(c: Container, user_id: int) -> WithdrawConfig:
    async with c.db() as session:
        user = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        available = await available_for_withdrawal(session, user, biz, c.clock.now())
    return WithdrawConfig(
        fees={n: fee_for(biz, n) for n in Network},
        min_amount=biz.withdraw_min_usdt,
        available=available,
        balance=user.balance,
        enabled=biz.withdrawals_enabled,
    )


def _base_checks(user: User, biz: BizSettings) -> None:
    if not biz.withdrawals_enabled:
        raise ServiceError("withdrawals_disabled", MSG_DISABLED, status=403)
    users_svc.ensure_active(user)


async def start(c: Container, user_id: int) -> WithdrawConfig:
    async with c.db() as session:
        user = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        _base_checks(user, biz)
    cfg = await get_config(c, user_id)
    if cfg.available < cfg.min_amount:
        raise ServiceError(
            "insufficient_for_withdrawal",
            f"Минимальная сумма вывода — {format_usdt(cfg.min_amount)} USDT. "
            f"Доступно к выводу: {format_usdt(cfg.available)} USDT.",
        )
    return cfg


async def check_address(session: AsyncSession, biz: BizSettings, network: Network, address: str) -> str:
    try:
        normalized = validate_address(network, address)
    except AddressError as exc:
        raise ServiceError("invalid_address", exc.message) from None
    if biz.b5_enabled:
        listed = (
            await session.execute(
                select(AddressBlacklist.id).where(
                    AddressBlacklist.network == network, func.lower(AddressBlacklist.address) == normalized.lower()
                )
            )
        ).first()
        if listed is not None:
            raise ServiceError("address_blacklisted", "Вывод на этот адрес невозможен. Обратитесь в поддержку.")
    return normalized


async def validate_address_for_user(c: Container, network: Network, address: str) -> str:
    async with c.db() as session:
        biz = await settings_svc.load(session)
        return await check_address(session, biz, network, address)


@dataclass(frozen=True)
class WithdrawPreview:
    network: Network
    address: str
    amount: Decimal
    fee: Decimal
    amount_to_send: Decimal


async def _validate(
    session: AsyncSession, user: User, biz: BizSettings, network: Network, address: str, amount: Decimal, now: datetime
) -> WithdrawPreview:
    _base_checks(user, biz)
    normalized = await check_address(session, biz, network, address)
    if amount != amount.quantize(CENT, rounding=ROUND_DOWN) or amount <= 0:
        raise ServiceError("invalid_amount", "Укажите сумму с точностью не более 2 знаков после точки.")
    if amount < biz.withdraw_min_usdt:
        raise ServiceError("below_minimum", f"Минимальная сумма вывода — {format_usdt(biz.withdraw_min_usdt)} USDT.")
    available = await available_for_withdrawal(session, user, biz, now)
    if amount > available:
        raise ServiceError(
            "insufficient_funds", f"Недостаточно средств. Доступно к выводу: {format_usdt(available)} USDT.", status=409
        )
    fee = fee_for(biz, network)
    to_send = amount - fee
    if to_send <= 0:
        raise ServiceError("amount_too_small", "Сумма должна быть больше комиссии сети.")
    await antifraud.check_withdrawal(session, user, biz, amount, now)
    return WithdrawPreview(network, normalized, amount, fee, to_send)


async def preview(c: Container, user_id: int, network: Network, address: str, amount: Decimal) -> WithdrawPreview:
    async with c.db() as session:
        user = await users_svc.get(session, user_id)
        biz = await settings_svc.load(session)
        return await _validate(session, user, biz, network, address, amount, c.clock.now())


@dataclass(frozen=True)
class CreateResult:
    withdrawal: Withdrawal
    created: bool


async def find_by_key(c: Container, user_id: int, key: str) -> Withdrawal | None:
    async with c.db() as session:
        result: Withdrawal | None = (
            await session.execute(
                select(Withdrawal).where(Withdrawal.user_id == user_id, Withdrawal.idempotency_key == key)
            )
        ).scalar_one_or_none()
        return result


async def create(
    c: Container, user_id: int, network: Network, address: str, amount: Decimal, *, idempotency_key: str | None
) -> CreateResult:
    now = c.clock.now()
    async with c.db.begin() as session:
        user = await users_svc.lock(session, user_id)
        if idempotency_key:
            existing = (
                await session.execute(
                    select(Withdrawal).where(
                        Withdrawal.user_id == user_id, Withdrawal.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return CreateResult(existing, False)
        biz = await settings_svc.load(session)
        pv = await _validate(session, user, biz, network, address, amount, now)
        w = Withdrawal(
            public_id=f"tmp-{user.id}-{now.timestamp()}",
            user_id=user.id,
            network=network,
            address=pv.address,
            amount=pv.amount,
            fee=pv.fee,
            amount_to_send=pv.amount_to_send,
            status=WithdrawalStatus.pending,
            idempotency_key=idempotency_key,
            created_at=now,
            verify_details={},
        )
        session.add(w)
        await session.flush()
        w.public_id = f"W-{100000 + w.id}"
        await ledger.hold(session, user, pv.amount, withdrawal_id=w.id, now=now)
        who = f"@{user.tg_username}" if user.tg_username else f"id {user.tg_id}"
        await notify.alert(
            session,
            f"📤 Новый вывод <b>{w.public_id}</b>: {format_usdt(w.amount_to_send)} USDT к отправке "
            f"({NETWORK[network]}), клиент {who}.",
            now=now,
        )
        shared_with = await antifraud.on_withdrawal_created(session, user, w, biz, now)
    if shared_with:
        await antifraud.flag_shared_address(c, shared_with, w.id)
    return CreateResult(w, True)


async def _lock(session: AsyncSession, withdrawal_id: int) -> Withdrawal:
    stmt = (
        select(Withdrawal)
        .where(Withdrawal.id == withdrawal_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    w: Withdrawal | None = (await session.execute(stmt)).scalar_one_or_none()
    if w is None:
        raise NotFound("Вывод не найден")
    return w


async def get_for_user(session: AsyncSession, user_id: int, withdrawal_id: int) -> Withdrawal:
    w = await session.get(Withdrawal, withdrawal_id)
    if w is None or w.user_id != user_id:
        raise NotFound("Вывод не найден")
    return w


def _client_text_rejected(w: Withdrawal, reason: str) -> str:
    return (
        f"❌ Вывод <b>{w.public_id}</b> отклонён.\nПричина: {reason}\n"
        f"Средства ({format_usdt(w.amount)} USDT) возвращены на баланс."
    )


async def _release(session: AsyncSession, w: Withdrawal, target: WithdrawalStatus, now: datetime) -> User:
    w.status = withdrawal_transition(w.status, target)
    user = await users_svc.lock(session, w.user_id)
    await ledger.release(session, user, w.amount, withdrawal_id=w.id, now=now)
    w.finished_at = now
    return user


async def cancel(c: Container, user_id: int, withdrawal_id: int) -> Withdrawal:
    now = c.clock.now()
    async with c.db.begin() as session:
        w = await _lock(session, withdrawal_id)
        if w.user_id != user_id:
            raise NotFound("Вывод не найден")
        if w.status != WithdrawalStatus.pending:
            raise Conflict("cannot_cancel", "Вывод уже взят в работу, отменить его нельзя.")
        await _release(session, w, WithdrawalStatus.cancelled, now)
        await notify.alert(session, f"Клиент отменил вывод {w.public_id}.", now=now)
        return w


# --- staff actions ----------------------------------------------------------------------


def _check_assignee(w: Withdrawal, actor: Actor) -> None:
    if w.assigned_staff_id != actor.id and not actor.is_admin:
        raise Forbidden("Заявка в работе у другого оператора")


def _check_b6(w: Withdrawal, actor: Actor, biz: BizSettings) -> None:
    if biz.b6_enabled and w.amount >= biz.withdraw_admin_only_from_usdt and not actor.is_admin:
        raise Forbidden(
            f"Выводы от {format_usdt(biz.withdraw_admin_only_from_usdt)} USDT закрывает только админ",
            code="admin_only_amount",
        )


async def take(c: Container, withdrawal_id: int, actor: Actor, ip: str | None = None) -> Withdrawal:
    now = c.clock.now()
    async with c.db.begin() as session:
        result = await session.execute(
            update(Withdrawal)
            .where(Withdrawal.id == withdrawal_id, Withdrawal.status == WithdrawalStatus.pending)
            .values(status=WithdrawalStatus.in_progress, assigned_staff_id=actor.id, taken_at=now)
            .returning(Withdrawal.id)
        )
        if result.scalar_one_or_none() is None:
            exists = await session.get(Withdrawal, withdrawal_id)
            if exists is None:
                raise NotFound("Вывод не найден")
            raise Conflict("already_taken", "Заявку уже взял другой оператор или она не в очереди.")
        await audit.write(
            session,
            action="withdrawal.take",
            staff_id=actor.id,
            entity_type="withdrawal",
            entity_id=withdrawal_id,
            ip=ip,
        )
        w = await _lock(session, withdrawal_id)
        return w


async def release_to_queue(c: Container, withdrawal_id: int, actor: Actor, ip: str | None = None) -> Withdrawal:
    async with c.db.begin() as session:
        w = await _lock(session, withdrawal_id)
        _check_assignee(w, actor)
        w.status = withdrawal_transition(w.status, WithdrawalStatus.pending)
        w.assigned_staff_id = None
        w.taken_at = None
        await audit.write(
            session, action="withdrawal.release", staff_id=actor.id, entity_type="withdrawal", entity_id=w.id, ip=ip
        )
        return w


async def reject(c: Container, withdrawal_id: int, actor: Actor, reason: str, ip: str | None = None) -> Withdrawal:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ServiceError("reason_required", "Укажите причину отклонения.")
    now = c.clock.now()
    async with c.db.begin() as session:
        w = await _lock(session, withdrawal_id)
        if w.status == WithdrawalStatus.in_progress:
            _check_assignee(w, actor)
        before = {"status": w.status.value}
        user = await _release(session, w, WithdrawalStatus.rejected, now)
        w.reject_reason = reason
        w.completed_staff_id = actor.id
        await notify.message(session, user.tg_id, _client_text_rejected(w, reason), now=now)
        await audit.write(
            session,
            action="withdrawal.reject",
            staff_id=actor.id,
            entity_type="withdrawal",
            entity_id=w.id,
            before=before,
            after={"status": w.status.value},
            comment=reason,
            ip=ip,
        )
        return w


@dataclass(frozen=True)
class SubmitResult:
    withdrawal: Withdrawal
    verify_status: VerifyStatus | None


async def submit_hash(
    c: Container, withdrawal_id: int, actor: Actor, tx_hash: str, ip: str | None = None
) -> SubmitResult:
    now = c.clock.now()
    async with c.db.begin() as session:
        w = await _lock(session, withdrawal_id)
        if w.status != WithdrawalStatus.in_progress:
            raise Conflict("not_in_progress", "Хэш вносится только для заявки в работе.")
        _check_assignee(w, actor)
        biz = await settings_svc.load(session)
        _check_b6(w, actor, biz)
        try:
            normalized = normalize_tx_hash(w.network, tx_hash)
        except TxHashError as exc:
            raise ServiceError("invalid_hash", exc.message) from None
        dup = (
            await session.execute(
                select(Withdrawal.public_id).where(
                    Withdrawal.network == w.network, Withdrawal.tx_hash == normalized, Withdrawal.id != w.id
                )
            )
        ).first()
        if dup is not None:
            raise Conflict(
                "duplicate_hash",
                f"Этот хэш уже использован в выводе {dup[0]}.",
                details={"verify_status": VerifyStatus.duplicate_hash.value},
            )
        w.tx_hash = normalized
        w.status = withdrawal_transition(w.status, WithdrawalStatus.verifying)
        w.hash_submitted_at = now
        w.verify_status = None
        w.verify_details = {}
        w.completed_staff_id = actor.id
        await audit.write(
            session,
            action="withdrawal.submit_hash",
            staff_id=actor.id,
            entity_type="withdrawal",
            entity_id=w.id,
            after={"tx_hash": normalized},
            ip=ip,
        )
    status = await verify_one(c, withdrawal_id)
    async with c.db() as session:
        fresh = await session.get(Withdrawal, withdrawal_id)
        assert fresh is not None
        return SubmitResult(fresh, status)


def _verifier(c: Container, network: Network) -> ChainVerifier:
    verifier: ChainVerifier = c.verifiers[network]
    return verifier


async def verify_one(c: Container, withdrawal_id: int) -> VerifyStatus | None:
    """Check the submitted hash in the blockchain (network call outside of transactions)."""
    async with c.db() as session:
        w = await session.get(Withdrawal, withdrawal_id)
        if w is None or w.status != WithdrawalStatus.verifying or w.tx_hash is None:
            return None
        tx_hash, network, address = w.tx_hash, w.network, w.address
        expected, created_at = w.amount_to_send, w.created_at
    verifier = _verifier(c, network)
    details: dict[str, Any] = {
        "tx_hash": tx_hash,
        "explorer_url": verifier.explorer_url(tx_hash),
        "expected": {"to": address, "amount": str(expected), "token": verifier.usdt_contract},
    }
    try:
        info = await asyncio.wait_for(verifier.get_usdt_transfer(tx_hash, address), timeout=VERIFY_TIMEOUT)
        status = evaluate(
            info,
            usdt_contract=verifier.usdt_contract,
            expected_to=address,
            expected_amount=expected,
            not_before=created_at,
            required_confirmations=verifier.required_confirmations,
            same_address=lambda a, b: same_address(network, a, b),
        )
        details["actual"] = {
            "found": info.found,
            "success": info.success,
            "to": info.to_address,
            "amount": None if info.amount is None else str(info.amount),
            "token": info.token_contract,
            "confirmations": info.confirmations,
            "block_time": info.block_time.isoformat() if info.block_time else None,
        }
    except (ChainUnavailable, TimeoutError) as exc:
        log.warning("chain_unavailable", network=network.value, error=str(exc)[:200])
        status = VerifyStatus.explorer_unavailable
        details["error"] = str(exc)[:300]
    return await _apply_verification(c, withdrawal_id, tx_hash, status, details)


async def _apply_verification(
    c: Container, withdrawal_id: int, tx_hash: str, status: VerifyStatus, details: dict[str, Any]
) -> VerifyStatus | None:
    now = c.clock.now()
    details["label"] = LABELS[status]
    details["checked_at"] = now.isoformat()
    async with c.db.begin() as session:
        w = await _lock(session, withdrawal_id)
        if w.status != WithdrawalStatus.verifying or w.tx_hash != tx_hash:
            return None  # state changed while we were asking the blockchain
        w.verify_status = status
        w.verify_details = details
        w.verify_attempts += 1
        w.last_verify_at = now
        if status == VerifyStatus.ok:
            w.status = withdrawal_transition(w.status, WithdrawalStatus.completed)
            user = await users_svc.lock(session, w.user_id)
            await ledger.complete(session, user, w.amount, withdrawal_id=w.id, now=now)
            w.finished_at = now
            await notify.message(
                session,
                user.tg_id,
                f"✅ Вывод <b>{w.public_id}</b> исполнен: {format_usdt(w.amount_to_send)} USDT "
                f"({NETWORK[w.network]}).\nХэш: <code>{tx_hash}</code>\n"
                f'<a href="{details["explorer_url"]}">Посмотреть в эксплорере</a>',
                now=now,
            )
            return status
        timed_out = (
            status == VerifyStatus.not_found
            and w.hash_submitted_at is not None
            and now - w.hash_submitted_at >= NOT_FOUND_TIMEOUT
        )
        if status in FINAL_FAILURES or timed_out:
            w.status = withdrawal_transition(w.status, WithdrawalStatus.in_progress)
            w.tx_hash = None  # the hash is kept in verify_details; the operator must enter another one
            await notify.alert(
                session,
                f"⚠️ Вывод {w.public_id}: проверка хэша не прошла — {LABELS[status]}. Нужен другой хэш.",
                now=now,
            )
        return status


async def recheck(c: Container, withdrawal_id: int, actor: Actor) -> VerifyStatus | None:
    async with c.db() as session:
        w = await session.get(Withdrawal, withdrawal_id)
        if w is None:
            raise NotFound("Вывод не найден")
        if w.status != WithdrawalStatus.verifying:
            raise Conflict("not_verifying", "Заявка не на проверке.")
    return await verify_one(c, withdrawal_id)


async def verify_due(c: Container, batch: int = 50) -> int:
    now = c.clock.now()
    async with c.db() as session:
        ids = (
            await session.execute(
                select(Withdrawal.id)
                .where(
                    Withdrawal.status == WithdrawalStatus.verifying,
                    (Withdrawal.last_verify_at.is_(None)) | (Withdrawal.last_verify_at <= now - RECHECK_EVERY),
                )
                .order_by(Withdrawal.last_verify_at.nulls_first())
                .limit(batch)
            )
        ).scalars()
        id_list = list(ids)
    for wid in id_list:
        try:
            await verify_one(c, wid)
        except Exception:
            log.exception("verify_failed", withdrawal_id=wid)
    return len(id_list)


async def force_complete(
    c: Container, withdrawal_id: int, actor: Actor, comment: str, ip: str | None = None
) -> Withdrawal:
    actor.require_admin()
    comment = (comment or "").strip()
    if len(comment) < 3:
        raise ServiceError("comment_required", "Комментарий обязателен.")
    now = c.clock.now()
    async with c.db.begin() as session:
        w = await _lock(session, withdrawal_id)
        if w.status not in (WithdrawalStatus.in_progress, WithdrawalStatus.verifying):
            raise Conflict("cannot_force", "Принудительно закрыть можно только заявку в работе или на проверке.")
        before = {"status": w.status.value, "tx_hash": w.tx_hash}
        if w.status == WithdrawalStatus.in_progress:
            w.status = withdrawal_transition(w.status, WithdrawalStatus.verifying)
        w.status = withdrawal_transition(w.status, WithdrawalStatus.completed)
        w.forced = True
        w.completed_staff_id = actor.id
        w.finished_at = now
        user = await users_svc.lock(session, w.user_id)
        await ledger.complete(session, user, w.amount, withdrawal_id=w.id, now=now)
        text = f"✅ Вывод <b>{w.public_id}</b> исполнен: {format_usdt(w.amount_to_send)} USDT ({NETWORK[w.network]})."
        if w.tx_hash:
            text += f"\nХэш: <code>{w.tx_hash}</code>"
        await notify.message(session, user.tg_id, text, now=now)
        await notify.alert(
            session, f"❗ Вывод {w.public_id} закрыт принудительно без проверки. Комментарий: {comment}", now=now
        )
        await audit.write(
            session,
            action="withdrawal.force_complete",
            staff_id=actor.id,
            entity_type="withdrawal",
            entity_id=w.id,
            before=before,
            after={"status": w.status.value, "forced": True},
            comment=comment,
            ip=ip,
        )
        return w
