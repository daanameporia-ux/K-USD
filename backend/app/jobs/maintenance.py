from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.services import auth, ledger, notify, orders, rates
from app.services import settings as settings_svc

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)


async def check_ledger_invariant(c: Container) -> int:
    now = c.clock.now()
    async with c.db.begin() as session:
        mismatches = await ledger.check_invariant(session)
        if not mismatches:
            return 0
        log.error("ledger_invariant_broken", users=[m.user_id for m in mismatches[:20]])
        biz = await settings_svc.load(session)
        if biz.withdrawals_enabled:
            await settings_svc.update(
                session, {"withdrawals_enabled": False}, staff_id=None, now=now, comment="ledger invariant broken"
            )
        details = ", ".join(
            f"user {m.user_id}: balance {m.balance} vs {m.ledger_balance}, hold {m.hold} vs {m.ledger_hold}"
            for m in mismatches[:5]
        )
        await notify.alert(
            session,
            f"🚨 КРИТИЧНО: расхождение балансов с журналом проводок ({len(mismatches)} клиентов). "
            f"Выводы автоматически выключены. {details}",
            now=now,
        )
        return len(mismatches)


async def cleanup(c: Container) -> None:
    now = c.clock.now()
    await c.dispatcher.cleanup_seen(timedelta(days=7))
    async with c.db.begin() as session:
        await rates.cleanup(session, now - timedelta(days=30))
        await auth.cleanup(session, now)
        await orders.cleanup_callbacks(session, now)


BACKUP_MAX_AGE = timedelta(hours=26)  # dumps are daily
BACKUP_ALERT_EVERY = timedelta(hours=12)


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def backup_problem(directory: Path, now: datetime, watching_since: datetime) -> str | None:
    """What is wrong with the database backups, judging by the marker files scripts/backup.sh leaves."""
    last_ok = _mtime(directory / ".last_ok")
    last_failed = _mtime(directory / ".last_failed")
    if last_failed is not None and (last_ok is None or last_failed > last_ok):
        return "последняя попытка резервного копирования завершилась ошибкой"
    if last_ok is None:
        # a fresh install makes its first dump within minutes: give it time before complaining
        return "нет ни одной успешной резервной копии" if now - watching_since > timedelta(hours=1) else None
    if now - last_ok > BACKUP_MAX_AGE:
        hours = int((now - last_ok).total_seconds() // 3600)
        return f"последняя успешная резервная копия сделана {hours} ч назад"
    return None


async def check_backups(c: Container) -> str | None:
    """Alert the staff when the daily database backup fails or goes stale (it used to be visible in logs only)."""
    if c.config.backup_dir is None:
        return None
    now = c.clock.now()
    problem = backup_problem(Path(c.config.backup_dir), now, c.started_at)
    if problem is None:
        c.throttle.reset("backup_problem")
        return None
    log.error("backup_problem", problem=problem)
    if c.throttle.allow("backup_problem", now, BACKUP_ALERT_EVERY):
        async with c.db.begin() as session:
            await notify.alert(
                session,
                f"🚨 Резервные копии базы: {problem}. Проверьте: docker compose logs backup",
                now=now,
            )
    return problem
