from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import StaffRole
from app.domain.errors import Forbidden


@dataclass(frozen=True)
class Actor:
    """A staff member performing an action."""

    id: int
    role: StaffRole
    is_superadmin: bool = False
    tg_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == StaffRole.admin

    def require_admin(self) -> None:
        if not self.is_admin:
            raise Forbidden("Действие доступно только админу")
