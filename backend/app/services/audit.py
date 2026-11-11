"""Audit log writer."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def write(
    session: AsyncSession,
    *,
    action: str,
    staff_id: int | None = None,
    entity_type: str | None = None,
    entity_id: object = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    comment: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        staff_id=staff_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before=before,
        after=after,
        comment=comment,
        ip=ip,
    )
    session.add(entry)
    await session.flush()
    return entry
