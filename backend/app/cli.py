"""Service commands: python -m app.cli <command>."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from app.config import ConfigError, load_config

if TYPE_CHECKING:
    from app.container import Container


def _container() -> Container:
    from app.container import Container

    return Container(load_config())


async def _check_ledger() -> int:
    from app.services import ledger

    c = _container()
    try:
        async with c.db() as session:
            mismatches = await ledger.check_invariant(session)
    finally:
        await c.aclose()
    for m in mismatches:
        print(f"user {m.user_id}: balance {m.balance} vs ledger {m.ledger_balance}; hold {m.hold} vs {m.ledger_hold}")
    print("OK: balances match the ledger" if not mismatches else f"MISMATCH: {len(mismatches)} users")
    return 1 if mismatches else 0


async def _set_webhook(delete: bool) -> int:
    c = _container()
    try:
        if delete:
            await c.tg.delete_webhook()
            print("webhook deleted")
        else:
            url = f"{c.config.public_base_url}/tg/webhook"
            await c.tg.set_webhook(url, c.config.telegram_webhook_secret)
            print(f"webhook set: {url}")
    finally:
        await c.aclose()
    return 0


async def _create_staff(tg_id: int, role: str) -> int:
    from sqlalchemy import select

    from app.domain.enums import StaffRole
    from app.models import Staff
    from app.services import audit

    c = _container()
    try:
        async with c.db.begin() as session:
            staff = (await session.execute(select(Staff).where(Staff.tg_id == tg_id))).scalar_one_or_none()
            if staff is None:
                staff = Staff(tg_id=tg_id, role=StaffRole(role), is_active=True)
                session.add(staff)
            else:
                staff.role = StaffRole(role)
                staff.is_active = True
            await session.flush()
            await audit.write(
                session,
                action="staff.cli_create",
                entity_type="staff",
                entity_id=staff.id,
                after={"tg_id": tg_id, "role": role},
            )
        print(f"staff {tg_id} is now an active {role}")
    finally:
        await c.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config", help="validate environment configuration")
    sub.add_parser("set-webhook", help="register the Telegram webhook")
    sub.add_parser("delete-webhook", help="remove the Telegram webhook")
    sub.add_parser("check-ledger", help="compare balances with the ledger")
    cs = sub.add_parser("create-staff", help="add or re-activate a staff member")
    cs.add_argument("--tg-id", type=int, required=True)
    cs.add_argument("--role", choices=["admin", "operator"], required=True)
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.cmd == "check-config":
        print(
            f"OK: ENV={cfg.env}, BOT_MODE={cfg.bot_mode}, PAYMENT_PROVIDER={cfg.payment_provider}, "
            f"PUBLIC_BASE_URL={cfg.public_base_url}"
        )
        return 0
    if args.cmd in ("set-webhook", "delete-webhook"):
        return asyncio.run(_set_webhook(args.cmd == "delete-webhook"))
    if args.cmd == "check-ledger":
        return asyncio.run(_check_ledger())
    return asyncio.run(_create_staff(args.tg_id, args.role))


if __name__ == "__main__":
    sys.exit(main())
