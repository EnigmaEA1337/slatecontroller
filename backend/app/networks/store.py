"""Async CRUD for the Networks catalog."""

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


class NetworkBuiltinError(NetworkError):
    """Cannot delete or rename a built-in network (lan, guest, iot)."""


def _to_public(row: NetworkRow) -> NetworkPublic:
    return NetworkPublic(
        slug=row.slug,
        display_name=row.display_name,
        bridge_name=row.bridge_name,
        subnet_cidr=row.subnet_cidr,
        gateway_ip=row.gateway_ip,
        dhcp_enabled=row.dhcp_enabled,
        isolated_from_lan=row.isolated_from_lan,
        vlan_tag=row.vlan_tag,
        is_builtin=row.is_builtin,
        notes=row.notes,
        ipv6_enabled=row.ipv6_enabled,
        ipv6_subnet_cidr=row.ipv6_subnet_cidr,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


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
        row = NetworkRow(
            slug=body.slug,
            display_name=body.display_name,
            bridge_name=body.bridge_name,
            subnet_cidr=body.subnet_cidr,
            gateway_ip=body.gateway_ip,
            dhcp_enabled=body.dhcp_enabled,
            isolated_from_lan=body.isolated_from_lan,
            vlan_tag=body.vlan_tag,
            is_builtin=False,
            notes=body.notes,
            ipv6_enabled=body.ipv6_enabled,
            ipv6_subnet_cidr=body.ipv6_subnet_cidr,
        )
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
            row.display_name = body.display_name
            row.bridge_name = body.bridge_name
            row.subnet_cidr = body.subnet_cidr
            row.gateway_ip = body.gateway_ip
            row.dhcp_enabled = body.dhcp_enabled
            row.isolated_from_lan = body.isolated_from_lan
            row.vlan_tag = body.vlan_tag
            row.notes = body.notes
            row.ipv6_enabled = body.ipv6_enabled
            row.ipv6_subnet_cidr = body.ipv6_subnet_cidr
            await session.commit()
            await session.refresh(row)
            return _to_public(row)

    async def delete(self, slug: str) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(NetworkRow).where(NetworkRow.slug == slug)
            )
            if row is None:
                raise NetworkNotFoundError(slug)
            if row.is_builtin:
                raise NetworkBuiltinError(
                    f"{slug!r} is a built-in network, cannot delete"
                )
            await session.execute(delete(NetworkRow).where(NetworkRow.slug == slug))
            await session.commit()

    async def seed_builtins(self, builtins: list[NetworkCreate]) -> int:
        """Seed the canonical lan/guest/iot networks. Skips existing slugs."""
        inserted = 0
        async with self._sf() as session:
            for nw in builtins:
                exists = await session.scalar(
                    select(NetworkRow.id).where(NetworkRow.slug == nw.slug)
                )
                if exists is not None:
                    continue
                session.add(
                    NetworkRow(
                        slug=nw.slug,
                        display_name=nw.display_name,
                        bridge_name=nw.bridge_name,
                        subnet_cidr=nw.subnet_cidr,
                        gateway_ip=nw.gateway_ip,
                        dhcp_enabled=nw.dhcp_enabled,
                        isolated_from_lan=nw.isolated_from_lan,
                        vlan_tag=nw.vlan_tag,
                        is_builtin=True,
                        notes=nw.notes,
                        ipv6_enabled=nw.ipv6_enabled,
                        ipv6_subnet_cidr=nw.ipv6_subnet_cidr,
                    )
                )
                inserted += 1
            if inserted:
                await session.commit()
        return inserted
