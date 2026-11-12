"""Minimal typed subset of Telegram Bot API objects (extra fields are ignored)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TgModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TgUser(TgModel):
    id: int
    is_bot: bool = False
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class Chat(TgModel):
    id: int
    type: str
    username: str | None = None
    title: str | None = None


class Message(TgModel):
    message_id: int
    date: int = 0
    chat: Chat
    from_user: TgUser | None = Field(None, alias="from")
    text: str | None = None
    caption: str | None = None
    reply_markup: dict[str, Any] | None = None


class CallbackQuery(TgModel):
    id: str
    from_user: TgUser = Field(alias="from")
    message: Message | None = None
    data: str | None = None


class ChatMember(TgModel):
    status: str
    user: TgUser


class ChatMemberUpdated(TgModel):
    chat: Chat
    from_user: TgUser = Field(alias="from")
    date: int = 0
    old_chat_member: ChatMember
    new_chat_member: ChatMember


class Update(TgModel):
    update_id: int
    message: Message | None = None
    edited_message: Message | None = None
    callback_query: CallbackQuery | None = None
    my_chat_member: ChatMemberUpdated | None = None

    @property
    def from_user(self) -> TgUser | None:
        if self.message is not None:
            return self.message.from_user
        if self.callback_query is not None:
            return self.callback_query.from_user
        if self.my_chat_member is not None:
            return self.my_chat_member.from_user
        return None

    @property
    def chat(self) -> Chat | None:
        if self.message is not None:
            return self.message.chat
        if self.callback_query is not None and self.callback_query.message is not None:
            return self.callback_query.message.chat
        if self.my_chat_member is not None:
            return self.my_chat_member.chat
        return None
