"""Bot configuration on startup: webhook, commands, menu button."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging import get_logger
from app.telegram import texts
from app.telegram.client import TelegramApiError

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)


async def configure_bot(c: Container, staff_tg_ids: list[int] | None = None) -> bool:
    cfg = c.config
    try:
        if cfg.bot_mode == "webhook":
            await c.tg.set_webhook(f"{cfg.public_base_url}/tg/webhook", cfg.telegram_webhook_secret)
        await c.tg.set_my_commands(texts.COMMANDS)
        await c.tg.set_chat_menu_button("Приложение", f"{cfg.public_base_url}/app/")
        for tg_id in staff_tg_ids or []:
            await set_staff_commands(c, tg_id)
    except TelegramApiError as exc:
        log.error("bot_setup_failed", method=exc.method, code=exc.code, description=exc.description)
        return False
    return True


async def set_staff_commands(c: Container, tg_id: int) -> None:
    try:
        await c.tg.set_my_commands(texts.STAFF_COMMANDS, scope={"type": "chat", "chat_id": tg_id})
    except TelegramApiError as exc:
        # The staff member may not have started the bot yet.
        log.info("staff_commands_not_set", tg_id=tg_id, code=exc.code)
