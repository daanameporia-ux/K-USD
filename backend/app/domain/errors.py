"""User-facing business errors with Russian messages (shown in bot and UI as is)."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    status: int = 400

    def __init__(self, code: str, message: str, *, status: int | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        if status is not None:
            self.status = status
        self.details = details or {}


class NotFound(ServiceError):
    status = 404

    def __init__(self, message: str = "Не найдено", code: str = "not_found") -> None:
        super().__init__(code, message)


class Forbidden(ServiceError):
    status = 403

    def __init__(self, message: str = "Недостаточно прав", code: str = "forbidden") -> None:
        super().__init__(code, message)


class Conflict(ServiceError):
    status = 409


class InvalidTransition(ServiceError):
    status = 409

    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(
            "invalid_transition",
            f"Недопустимое действие для текущего статуса ({current} → {target})",
            details={"entity": entity, "from": current, "to": target},
        )
