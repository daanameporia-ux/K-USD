"""KYC extension point. See docs/KYC.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.models import User


@dataclass(frozen=True)
class KycStep:
    """What to do next in the registration flow.

    status:
      - "passed": registration complete, the user becomes active;
      - "rejected": KYC failed, show `message`;
      - "input_required": show `message` and wait for the next input (`expects` hints the kind of input).
    """

    status: Literal["passed", "rejected", "input_required"]
    message: str | None = None
    expects: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KycInput:
    """User input for a multi-step questionnaire, coming from the bot (text/contact) or the Mini App (form)."""

    text: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class KycProvider(Protocol):
    async def start(self, user: User) -> KycStep: ...

    async def handle_input(self, user: User, update: KycInput) -> KycStep: ...

    async def is_passed(self, user: User) -> bool: ...
