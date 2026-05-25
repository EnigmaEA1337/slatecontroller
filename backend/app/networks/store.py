"""Async CRUD for the Networks catalog.

V2 (2026-05-26) : removed the `is_builtin` concept and the seeding of
"canonical" lan/guest/iot networks. Fresh installs start with an empty
catalog — every network is user-created and freely deletable.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import NetworkRow
from app.exceptions import SlateError
from app.networks.models import NetworkCreate, NetworkPublic, NetworkWrite


class NetworkError(SlateError):
    pass


class NetworkNotFoundError(NetworkError):
    pass


class NetworkDuplicateError(NetworkError):
    pass


def _to_public(row: NetworkRow) -> NetworkPublic:
    return NetworkPublic(
        slug=row.slug,
        display_name=row.display_name,
        bridge_name=row.bridge_name,
        subnet_cidr=row.subnet_cidr,
        gateway_ip=row.gateway_ip,
        dhcp_enabled=row.dhcp_enabled,
        vlan_tag=row.vlan_tag,
        notes=row.notes,
        ipv6_enabled=row.ipv6_enabled,
        ipv6_subnet_cidr=row.ipv6_subnet_cidr,
        intra_bridge_isolation=row.intra_bridge_isolation,
        reach_internet=row.reach_internet,
        reachable_networks=list(row.reachable_networks or []),
        admin_access=row.admin_access,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _copy_write_fields(row: NetworkRow, body: NetworkWrite) -> None:
    """Mirror every NetworkWrite field onto the ORM row. Used by both
    create + update so the two stay in sync as the schema grows."""
    row.display_name = body.display_name
    row.bridge_name = body.bridge_name
    row.subnet_cidr = body.subnet_cidr
    row.gateway_ip = body.gateway_ip
    row.dhcp_enabled = body.dhcp_enabled
    row.vlan_tag = body.vlan_tag
    row.notes = body.notes
    row.ipv6_enabled = body.ipv6_enabled
    row.ipv6_subnet_cidr = body.ipv6_subnet_cidr
    row.intra_bridge_isolation = body.intra_bridge_isolation
    row.reach_internet = body.reach_internet
    row.reachable_networks = list(body.reachable_networks or [])
    row.admin_access = body.admin_access


class NetworkStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_all(self) -> list[NetworkPublic]:
        async with self._sf() as session:
            rows = (
                (await session.execute(select(NetworkRow).order_by(NetworkRow.slug)))
                .scalars()
                .all()
            )
            return [_to_public(r) for r in rows]

    async def get(self, slug: str) -> NetworkPublic:
        async with self._sf() as session:
            row = await session.scalar(
                select(NetworkRow).where(NetworkRow.slug == slug)
            )
            if row is None:
                raise NetworkNotFoundError(slug)
            return _to_public(row)

    async def create(self, body: NetworkCreate) -> NetworkPublic:
        row = NetworkRow(slug=body.slug)
        _copy_write_fields(row, body)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise NetworkDuplicateError(body.slug) from exc
            await session.refresh(row)
            return _to_public(row)

    async def update(self, slug: str, body: NetworkWrite) -> NetworkPublic:
        async with self._sf() as session:
            row = await session.scalar(
                select(NetworkRow).where(NetworkRow.slug == slug)
            )
            if row is None:
                raise NetworkNotFoundError(slug)
            _copy_write_fields(row, body)
            await session.commit()
            await session.refresh(row)
            return _to_public(row)

    async def delete(self, slug: str) -> None:
        """Delete a network. No builtin guard — every network is now
        user-managed (cf module docstring)."""
        async with self._sf() as session:
            row = await session.scalar(
                select(NetworkRow).where(NetworkRow.slug == slug)
            )
            if row is None:
                raise NetworkNotFoundError(slug)
            await session.execute(delete(NetworkRow).where(NetworkRow.slug == slug))
            await session.commit()
