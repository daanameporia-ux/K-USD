from __future__ import annotations

import httpx
import pytest

from app.telegram.client import (
    BotBlockedError,
    TelegramApiError,
    TelegramClient,
    TelegramNetworkError,
    escape_html,
)
from tests.conftest import SleepRecorder
from tests.fakes import FakeTelegram

TOKEN = "123456:SECRET-TOKEN-abcdefghijklmnopqrstu"


def make(fake: FakeTelegram, sleep: SleepRecorder, blocked: list[int] | None = None) -> TelegramClient:
    async def on_blocked(chat_id: int) -> None:
        if blocked is not None:
            blocked.append(chat_id)

    return TelegramClient(
        TOKEN,
        base_url="https://tg.fake",
        transport=fake.transport(),
        sleep=sleep,
        on_bot_blocked=on_blocked,
        backoff_base=0.5,
    )


async def test_send_message_uses_html_and_returns_result() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    client = make(fake, sleep)
    result = await client.send_message(5, "<b>hi</b>", {"inline_keyboard": []})
    assert result is not None and result["chat"]["id"] == 5
    call = fake.of("sendMessage")[0]
    assert call.params["parse_mode"] == "HTML"
    assert call.params["reply_markup"] == {"inline_keyboard": []}


async def test_429_waits_retry_after_and_retries() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    fake.script(
        "sendMessage",
        {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}},
    )
    client = make(fake, sleep)
    await client.send_message(1, "x")
    assert sleep.calls == [7.0]
    assert len(fake.of("sendMessage")) == 2


async def test_429_gives_up_after_three_retries() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    err = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 1}}
    fake.script("sendMessage", err, err, err, err)
    client = make(fake, sleep)
    with pytest.raises(TelegramApiError) as exc:
        await client.send_message(1, "x")
    assert exc.value.code == 429
    assert len(fake.of("sendMessage")) == 4


async def test_5xx_and_network_errors_retry_with_backoff() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    fake.script("getMe", {"ok": False, "error_code": 502, "description": "Bad Gateway"}, httpx.ConnectError("boom"))
    client = make(fake, sleep)
    me = await client.get_me()
    assert me["username"] == "test_bot"
    assert sleep.calls == [0.5, 1.0]


async def test_network_error_after_retries_masks_token() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    fake.script("getMe", *[httpx.ConnectError(f"cannot reach https://tg.fake/bot{TOKEN}/getMe")] * 4)
    client = make(fake, sleep)
    with pytest.raises(TelegramNetworkError) as exc:
        await client.get_me()
    assert TOKEN not in str(exc.value)
    assert exc.value.__cause__ is None


async def test_403_marks_bot_blocked() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    fake.script(
        "sendMessage", {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}
    )
    blocked: list[int] = []
    client = make(fake, sleep, blocked)
    with pytest.raises(BotBlockedError):
        await client.send_message(42, "x")
    assert blocked == [42]


async def test_message_not_modified_is_ignored() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    fake.script(
        "editMessageText", {"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"}
    )
    client = make(fake, sleep)
    assert await client.edit_message_text(1, 2, "same") is None


async def test_other_errors_raise_typed_exception() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    fake.script("sendMessage", {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
    client = make(fake, sleep)
    with pytest.raises(TelegramApiError) as exc:
        await client.send_message(1, "x")
    assert exc.value.code == 400
    assert "chat not found" in exc.value.description
    assert TOKEN not in str(exc.value)


async def test_send_photo_is_multipart() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    client = make(fake, sleep)
    await client.send_photo(9, b"\x89PNGdata", "cap", {"inline_keyboard": [[{"text": "a", "url": "https://x"}]]})
    call = fake.of("sendPhoto")[0]
    assert call.params["photo"] == b"\x89PNGdata"
    assert call.params["caption"] == "cap"
    assert call.params["reply_markup"]["inline_keyboard"][0][0]["text"] == "a"


async def test_other_methods_payloads() -> None:
    fake, sleep = FakeTelegram(), SleepRecorder()
    client = make(fake, sleep)
    await client.set_webhook("https://x/tg/webhook", "sec")
    await client.delete_webhook()
    await client.get_updates(10, timeout=30)
    await client.edit_message_reply_markup(1, 2)
    await client.delete_message(1, 2)
    await client.answer_callback_query("cb", "t", alert=True)
    await client.set_my_commands([("start", "Начать")])
    await client.set_chat_menu_button("Открыть", "https://x/app/")
    methods = [c.method for c in fake.calls]
    assert methods == [
        "setWebhook",
        "deleteWebhook",
        "getUpdates",
        "editMessageReplyMarkup",
        "deleteMessage",
        "answerCallbackQuery",
        "setMyCommands",
        "setChatMenuButton",
    ]
    assert fake.calls[0].params["allowed_updates"] == ["message", "callback_query", "my_chat_member"]
    assert fake.calls[2].params["offset"] == 10
    assert fake.calls[3].params["reply_markup"] == {"inline_keyboard": []}
    assert fake.calls[7].params["menu_button"]["web_app"]["url"] == "https://x/app/"
    await client.aclose()


def test_escape_html() -> None:
    assert escape_html('<a href="x">&</a>') == "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;"
    assert escape_html(None) == ""


def test_mask_secrets_in_logs() -> None:
    from app.logging import mask_secrets

    assert TOKEN not in mask_secrets(f"POST https://api.telegram.org/bot{TOKEN}/sendMessage")
