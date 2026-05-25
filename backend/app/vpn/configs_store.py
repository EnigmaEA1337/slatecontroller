"""Async CRUD for stored VPN configs (DB layer).

Owns the boundary between the ORM rows and the typed Pydantic models exposed
by the API. The private key is decrypted only when explicitly requested
(`get_private_key`) so the rest of the code path never sees it.
"""

from __future__ import annotations

import re
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import VPNConfigRow
from app.exceptions import SlateError
from app.models.vpn_config import VPNConfigPublic, VpnProvider
from app.vpn.crypto import decrypt, encrypt
from app.vpn.wg_parser import ParsedWGConfig

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


class VPNConfigError(SlateError):
    """Domain errors for the configs store."""


class VPNConfigNotFoundError(VPNConfigError):
    """Asked for a name that isn't in the store."""


class VPNConfigDuplicateError(VPNConfigError):
    """A config with that name already exists."""


class VPNConfigStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def normalize_name(raw: str) -> str:
        """Slug the user-supplied name. Raises if it can't be made a valid slug."""
        slug = raw.strip().lower().replace(" ", "-")
        slug = re.sub(r"[^a-z0-9_.-]", "", slug)
        if not _NAME_PATTERN.match(slug):
            raise VPNConfigError(
                "name must be 1-63 chars of [a-z0-9_.-], start with a letter or digit"
            )
        return slug

    @staticmethod
    def _row_to_public(row: VPNConfigRow) -> VPNConfigPublic:
        return VPNConfigPublic(
            name=row.name,
            provider=cast(VpnProvider, row.provider),
            interface_address=row.interface_address,
            dns_servers=[s for s in row.dns_servers.split(",") if s],
            peer_public_key=row.peer_public_key,
            peer_endpoint=row.peer_endpoint,
            peer_allowed_ips=[s for s in row.peer_allowed_ips.split(",") if s],
            created_at=row.created_at,
        )

    async def list_all(self) -> list[VPNConfigPublic]:
        async with self._sf() as session:
            result = await session.execute(
                select(VPNConfigRow).order_by(VPNConfigRow.created_at.desc())
            )
            return [self._row_to_public(r) for r in result.scalars().all()]

    async def get(self, name: str) -> VPNConfigPublic:
        async with self._sf() as session:
            row = await session.scalar(
                select(VPNConfigRow).where(VPNConfigRow.name == name)
            )
            if row is None:
                raise VPNConfigNotFoundError(name)
            return self._row_to_public(row)

    async def add(
        self,
        *,
        name: str,
        provider: VpnProvider,
        config: ParsedWGConfig,
    ) -> VPNConfigPublic:
        slug = self.normalize_name(name)
        row = VPNConfigRow(
            name=slug,
            provider=provider,
            interface_address=config.interface_address,
            dns_servers=",".join(config.interface_dns),
            peer_public_key=config.peer_public_key,
            peer_endpoint=config.peer_endpoint,
            peer_allowed_ips=",".join(config.peer_allowed_ips),
            private_key_encrypted=encrypt(config.interface_private_key),
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise VPNConfigDuplicateError(slug) from exc
            await session.refresh(row)
            return self._row_to_public(row)

    async def delete(self, name: str) -> None:
        async with self._sf() as session:
            result = await session.execute(
                delete(VPNConfigRow).where(VPNConfigRow.name == name)
            )
            if result.rowcount == 0:
                raise VPNConfigNotFoundError(name)
            await session.commit()

    async def get_private_key(self, name: str) -> str:
        """Decrypt and return the stored private key. Use sparingly."""
        async with self._sf() as session:
            row = await session.scalar(
                select(VPNConfigRow).where(VPNConfigRow.name == name)
            )
            if row is None:
                raise VPNConfigNotFoundError(name)
            return decrypt(row.private_key_encrypted)
