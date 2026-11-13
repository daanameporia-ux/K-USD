"""Address book: withdrawal addresses a client saved to reuse them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.addresses import same_address
from app.domain.enums import Network
from app.domain.errors import NotFound, ServiceError
from app.models import SavedAddress
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.services import withdrawals

if TYPE_CHECKING:
    from app.container import Container

MAX_ADDRESSES = 20
MAX_LABEL = 40


def clean_label(raw: str | None) -> str:
    label = " ".join((raw or "").split())
    if len(label) > MAX_LABEL:
        raise ServiceError("invalid_label", f"Название адреса — не длиннее {MAX_LABEL} символов.")
    return label


def short(address: str) -> str:
    return address if len(address) <= 16 else f"{address[:6]}…{address[-6:]}"


def title(item: SavedAddress) -> str:
    return f"{item.label} · {short(item.address)}" if item.label else short(item.address)


async def list_for(session: AsyncSession, user_id: int, networks: list[Network]) -> list[SavedAddress]:
    """Saved addresses of the networks that are offered now, newest first."""
    stmt = (
        select(SavedAddress)
        .where(SavedAddress.user_id == user_id, SavedAddress.network.in_(networks))
        .order_by(SavedAddress.id.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get(session: AsyncSession, user_id: int, address_id: int) -> SavedAddress:
    item = await session.get(SavedAddress, address_id)
    if item is None or item.user_id != user_id:
        raise NotFound("Адрес не найден")
    return item


async def find(session: AsyncSession, user_id: int, network: Network, address: str) -> SavedAddress | None:
    rows = await session.execute(
        select(SavedAddress).where(SavedAddress.user_id == user_id, SavedAddress.network == network)
    )
    return next((r for r in rows.scalars() if same_address(network, r.address, address)), None)


async def add(c: Container, user_id: int, network: Network, address: str, label: str | None = None) -> SavedAddress:
    """Save an address (validated like for a withdrawal). Saving it again only renames it."""
    withdrawals.ensure_network(c, network)
    name = clean_label(label)
    async with c.db.begin() as session:
        await users_svc.lock(session, user_id)  # serializes the limit check
        biz = await settings_svc.load(session)
        normalized = await withdrawals.check_address(session, biz, network, address)
        existing = await find(session, user_id, network, normalized)
        if existing is not None:
            if label is not None:
                existing.label = name
            return existing
        count = (
            await session.execute(select(func.count()).select_from(SavedAddress).where(SavedAddress.user_id == user_id))
        ).scalar_one()
        if count >= MAX_ADDRESSES:
            raise ServiceError(
                "too_many_addresses", f"В книге адресов уже {MAX_ADDRESSES} адресов. Удалите ненужные.", status=409
            )
        stmt = (
            insert(SavedAddress)
            .values(user_id=user_id, network=network, address=normalized, label=name, created_at=c.clock.now())
            .returning(SavedAddress)
        )
        saved: SavedAddress = (await session.execute(stmt)).scalar_one()
        return saved


async def remove(c: Container, user_id: int, address_id: int) -> None:
    async with c.db.begin() as session:
        await session.delete(await get(session, user_id, address_id))
