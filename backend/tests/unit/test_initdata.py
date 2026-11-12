from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.domain.initdata import InitDataError, sign, verify

TOKEN = "123456:TEST-TOKEN-abcdefghijklmnopqrstuvwxyz"
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def make(tg_id=42, *, auth_date=None, token=TOKEN, extra=None, order=None) -> str:
    fields = {
        "query_id": "AAH",
        "user": json.dumps({"id": tg_id, "first_name": "Ann", "username": "ann", "language_code": "ru"}),
        "auth_date": str(int((auth_date or NOW).timestamp())),
        **(extra or {}),
    }
    fields["hash"] = sign(fields, token)
    items = list(fields.items())
    if order == "reversed":
        items.reverse()
    return urlencode(items)


def test_valid() -> None:
    user = verify(make(), TOKEN, NOW)
    assert user.id == 42 and user.username == "ann" and user.auth_date == NOW


def test_field_order_does_not_matter() -> None:
    assert verify(make(order="reversed"), TOKEN, NOW).id == 42


def test_modified_field() -> None:
    data = make().replace("Ann", "Bob")
    with pytest.raises(InitDataError):
        verify(data, TOKEN, NOW)


def test_other_bot_token() -> None:
    with pytest.raises(InitDataError):
        verify(make(token="999:OTHER-TOKEN-zzzzzzzzzzzzzzzzzzzzzz"), TOKEN, NOW)


def test_expired_auth_date() -> None:
    assert verify(make(auth_date=NOW - timedelta(minutes=10)), TOKEN, NOW)
    with pytest.raises(InitDataError):
        verify(make(auth_date=NOW - timedelta(minutes=10, seconds=1)), TOKEN, NOW)
    with pytest.raises(InitDataError):
        verify(make(auth_date=NOW + timedelta(minutes=5)), TOKEN, NOW)


def test_missing_hash_and_garbage() -> None:
    data = "&".join(p for p in make().split("&") if not p.startswith("hash="))
    with pytest.raises(InitDataError):
        verify(data, TOKEN, NOW)
    with pytest.raises(InitDataError):
        verify("", TOKEN, NOW)
    with pytest.raises(InitDataError):
        verify(make() + "&hash=00", TOKEN, NOW)
    no_user = {"auth_date": str(int(NOW.timestamp()))}
    no_user["hash"] = sign(no_user, TOKEN)
    with pytest.raises(InitDataError):
        verify(urlencode(no_user), TOKEN, NOW)
