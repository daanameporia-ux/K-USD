from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.services import auth, ledger, notify, rates
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
