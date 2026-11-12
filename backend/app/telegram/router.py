"""Own update router: commands, button texts, callback prefixes and FSM states."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging import get_logger
from app.telegram.client import TelegramApiError, TelegramClient
from app.telegram.fsm import FSM
from app.telegram.types import CallbackQuery, Message, Update

if TYPE_CHECKING:
    from app.container import Container
    from app.models import User

Handler = Callable[["Ctx"], Awaitable[None]]

log = get_logger(__name__)


@dataclass
class Ctx:
    update: Update
    container: Container
    tg: TelegramClient
    tg_id: int
    chat_id: int
    fsm: FSM
    user: User | None = None
    args: str = ""
    callback_answered: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> Message | None:
        return self.update.message

    @property
    def callback(self) -> CallbackQuery | None:
        return self.update.callback_query

    @property
    def text(self) -> str:
        msg = self.update.message
        return (msg.text or "").strip() if msg is not None else ""

    @property
    def cb_data(self) -> str:
        cb = self.update.callback_query
        return (cb.data or "") if cb is not None else ""

    @property
    def state(self) -> str | None:
        return self.fsm.state

    async def reply(self, text: str, markup: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return await self.tg.send_message(self.chat_id, text, markup)

    async def answer(self, text: str | None = None, alert: bool = False) -> None:
        cb = self.update.callback_query
        if cb is None or self.callback_answered:
            return
        self.callback_answered = True
        try:
            await self.tg.answer_callback_query(cb.id, text, alert)
        except TelegramApiError as exc:
            # cosmetic only (e.g. "query is too old" after a slow provider call): never abort the handler
            log.info("callback_answer_failed", code=exc.code, description=exc.description[:100])

    async def edit(self, text: str, markup: dict[str, Any] | None = None) -> None:
        cb = self.update.callback_query
        if cb is None or cb.message is None:
            await self.reply(text, markup)
            return
        if cb.message.text is None:
            # Media message (e.g. a QR photo): send a new message instead of editing the caption.
            await self.reply(text, markup)
            return
        await self.tg.edit_message_text(self.chat_id, cb.message.message_id, text, markup)


@dataclass
class _Route:
    handler: Handler
    resets_state: bool


class Router:
    def __init__(self) -> None:
        self._commands: dict[str, _Route] = {}
        self._texts: dict[str, _Route] = {}
        self._callbacks: list[tuple[str, Handler]] = []
        self._states: dict[str, Handler] = {}
        self._chat_member: Handler | None = None
        self._fallback: Handler | None = None

    def command(self, *names: str, resets_state: bool = True) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            for name in names:
                self._commands[name.lstrip("/").lower()] = _Route(fn, resets_state)
            return fn

        return deco

    def text(self, *texts: str, resets_state: bool = True) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            for t in texts:
                self._texts[t] = _Route(fn, resets_state)
            return fn

        return deco

    def callback(self, prefix: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._callbacks.append((prefix, fn))
            self._callbacks.sort(key=lambda item: len(item[0]), reverse=True)
            return fn

        return deco

    def state(self, *states: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            for s in states:
                self._states[s] = fn
            return fn

        return deco

    def chat_member(self) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._chat_member = fn
            return fn

        return deco

    def fallback(self) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._fallback = fn
            return fn

        return deco

    def include(self, other: Router) -> None:
        self._commands.update(other._commands)
        self._texts.update(other._texts)
        for prefix, fn in other._callbacks:
            self.callback(prefix)(fn)
        self._states.update(other._states)
        self._chat_member = other._chat_member or self._chat_member
        self._fallback = other._fallback or self._fallback

    def resolve(self, ctx: Ctx) -> tuple[Handler | None, bool]:
        """Return the handler and whether navigation should reset FSM state."""
        update = ctx.update
        if update.my_chat_member is not None:
            return self._chat_member, False
        if update.callback_query is not None:
            data = ctx.cb_data
            for prefix, fn in self._callbacks:
                if data.startswith(prefix):
                    return fn, False
            return self._fallback, False
        if update.message is None:
            return None, False
        text = ctx.text
        if text.startswith("/"):
            head, _, rest = text.partition(" ")
            name = head[1:].split("@", 1)[0].lower()
            ctx.args = rest.strip()
            route = self._commands.get(name)
            if route is not None:
                return route.handler, route.resets_state
            return self._fallback, False
        route = self._texts.get(text)
        if route is not None:
            return route.handler, route.resets_state
        if ctx.state is not None and ctx.state in self._states:
            return self._states[ctx.state], False
        return self._fallback, False
