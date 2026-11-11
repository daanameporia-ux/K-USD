"""Escaping of free text placed into Telegram messages with parse_mode=HTML."""

from __future__ import annotations


def escape_html(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
