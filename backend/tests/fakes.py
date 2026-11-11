"""Fake external services for tests: Telegram Bot API server."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

_METHOD_RE = re.compile(r"/bot[^/]+/(\w+)$")


def _parse_multipart(content: bytes, content_type: str) -> dict[str, Any]:
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
    result: dict[str, Any] = {}
    for part in content.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        name_match = re.search(rb'name="([^"]+)"', head)
        if not name_match:
            continue
        name = name_match.group(1).decode()
        if b"filename=" in head:
            result[name] = body
        else:
            value = body.decode()
            try:
                result[name] = json.loads(value) if value[:1] in "{[" else value
            except ValueError:
                result[name] = value
    return result


@dataclass
class Call:
    method: str
    params: dict[str, Any]


@dataclass
class FakeTelegram:
    """Records outgoing Bot API calls; responses can be scripted per method."""

    calls: list[Call] = field(default_factory=list)
    scripted: dict[str, list[Any]] = field(default_factory=dict)
    next_message_id: int = 1000
    updates_queue: list[dict[str, Any]] = field(default_factory=list)

    def script(self, method: str, *responses: Any) -> None:
        """Queue responses: dict -> JSON body, int -> status code w/ error, callable -> raise/produce."""
        self.scripted.setdefault(method, []).extend(responses)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        match = _METHOD_RE.search(request.url.path)
        method = match.group(1) if match else "?"
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            params = _parse_multipart(request.content, ctype)
        else:
            params = json.loads(request.content or b"{}")
        self.calls.append(Call(method, params))
        queue = self.scripted.get(method)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Callable):  # type: ignore[arg-type]
                item = item(request)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, httpx.Response):
                return item
            status = item.get("error_code", 200) if not item.get("ok", True) else 200
            return httpx.Response(status, json=item)
        return httpx.Response(200, json={"ok": True, "result": self._default_result(method, params)})

    def _default_result(self, method: str, params: dict[str, Any]) -> Any:
        if method in ("sendMessage", "sendPhoto"):
            self.next_message_id += 1
            chat_id = params.get("chat_id")
            return {
                "message_id": self.next_message_id,
                "date": 0,
                "chat": {"id": int(chat_id) if chat_id is not None else 0, "type": "private"},
                "text": params.get("text"),
            }
        if method == "getUpdates":
            updates, self.updates_queue = self.updates_queue, []
            return updates
        if method == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "Bot", "username": "test_bot"}
        return True

    # --- helpers ---------------------------------------------------------------

    def of(self, method: str) -> list[Call]:
        return [c for c in self.calls if c.method == method]

    def sent_to(self, chat_id: int) -> list[Call]:
        return [
            c
            for c in self.calls
            if c.method in ("sendMessage", "sendPhoto", "editMessageText", "editMessageReplyMarkup")
            and int(c.params.get("chat_id", 0)) == chat_id
        ]

    def texts_to(self, chat_id: int) -> list[str]:
        out = []
        for c in self.sent_to(chat_id):
            t = c.params.get("text") or c.params.get("caption")
            if t:
                out.append(str(t))
        return out

    def last_text(self, chat_id: int) -> str:
        texts = self.texts_to(chat_id)
        return texts[-1] if texts else ""

    def last_markup(self, chat_id: int) -> dict[str, Any] | None:
        for c in reversed(self.sent_to(chat_id)):
            if "reply_markup" in c.params:
                markup: dict[str, Any] = c.params["reply_markup"]
                return markup
        return None

    def reset(self) -> None:
        self.calls.clear()


class UpdateFactory:
    def __init__(self) -> None:
        self._update_id = 0
        self._message_id = 0

    def _next(self) -> int:
        self._update_id += 1
        return self._update_id

    def message(
        self,
        tg_id: int,
        text: str,
        *,
        username: str | None = None,
        chat_type: str = "private",
        chat_id: int | None = None,
        update_id: int | None = None,
    ) -> dict[str, Any]:
        self._message_id += 1
        return {
            "update_id": update_id if update_id is not None else self._next(),
            "message": {
                "message_id": self._message_id,
                "date": 0,
                "chat": {"id": chat_id if chat_id is not None else tg_id, "type": chat_type},
                "from": {
                    "id": tg_id,
                    "is_bot": False,
                    "first_name": "User",
                    "username": username,
                    "language_code": "ru",
                },
                "text": text,
            },
        }

    def callback(
        self,
        tg_id: int,
        data: str,
        *,
        message_id: int = 1,
        with_text: bool = True,
        update_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"message_id": message_id, "date": 0, "chat": {"id": tg_id, "type": "private"}}
        if with_text:
            message["text"] = "..."
        return {
            "update_id": update_id if update_id is not None else self._next(),
            "callback_query": {
                "id": f"cb{self._update_id}",
                "from": {"id": tg_id, "is_bot": False, "first_name": "User", "username": username},
                "message": message,
                "data": data,
            },
        }

    def chat_member(self, tg_id: int, status: str) -> dict[str, Any]:
        user = {"id": tg_id, "is_bot": False, "first_name": "User"}
        return {
            "update_id": self._next(),
            "my_chat_member": {
                "chat": {"id": tg_id, "type": "private"},
                "from": user,
                "date": 0,
                "old_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True, "first_name": "B"}},
                "new_chat_member": {"status": status, "user": {"id": 1, "is_bot": True, "first_name": "B"}},
            },
        }
