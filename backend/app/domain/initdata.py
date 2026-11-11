"""Telegram Mini App initData verification (https://core.telegram.org/bots/webapps)."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

MAX_AGE = timedelta(minutes=10)


class InitDataError(ValueError):
    pass


@dataclass(frozen=True)
class InitDataUser:
    id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    auth_date: datetime


def sign(fields: dict[str, str], bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def verify(init_data: str, bot_token: str, now: datetime, max_age: timedelta = MAX_AGE) -> InitDataUser:
    if not init_data:
        raise InitDataError("empty initData")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError(f"duplicate field {key}")
        fields[key] = value
    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("hash is missing")
    expected = sign(fields, bot_token)
    if not hmac.compare_digest(expected, received.lower()):
        raise InitDataError("bad signature")
    try:
        auth_date = datetime.fromtimestamp(int(fields["auth_date"]), tz=now.tzinfo)
    except (KeyError, ValueError) as exc:
        raise InitDataError("auth_date is missing") from exc
    if now - auth_date > max_age:
        raise InitDataError("initData is too old")
    if auth_date - now > timedelta(minutes=1):
        raise InitDataError("auth_date is in the future")
    try:
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InitDataError("user is missing") from exc
    return InitDataUser(
        id=user_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        language_code=user.get("language_code"),
        auth_date=auth_date,
    )
