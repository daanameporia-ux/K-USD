"""Keyboard builders."""

from __future__ import annotations

from typing import Any

from app.telegram import texts


def main_menu(app_url: str) -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": texts.BTN_BUY}, {"text": texts.BTN_BALANCE}],
            [{"text": texts.BTN_WITHDRAW}, {"text": texts.BTN_HISTORY}],
            [{"text": texts.BTN_SUPPORT}, {"text": texts.BTN_APP, "web_app": {"url": app_url}}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def inline(*rows: list[tuple[str, str]]) -> dict[str, Any]:
    """Rows of (text, callback_data) buttons."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def button_url(text: str, url: str) -> dict[str, Any]:
    return {"text": text, "url": url}


def button_cb(text: str, data: str) -> dict[str, Any]:
    return {"text": text, "callback_data": data}


def web_app_button(text: str, url: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": text, "web_app": {"url": url}}]]}


def register() -> dict[str, Any]:
    return inline([(texts.BTN_REGISTER, "reg:start")])


REMOVE = {"remove_keyboard": True}
