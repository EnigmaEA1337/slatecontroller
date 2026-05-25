"""DB store for editable DNS security levels.

Wraps the `dns_security_levels` table. Seeds it from
`security_levels.FACTORY_LEVELS` at app boot (idempotent). All runtime
consumers (the manager, the API routes) read from here — the Python
constant is only used for the initial seed and for "reset to factory".
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DnsSecurityLevelRow
from app.dns.security_levels import (
    FACTORY_LEVELS,
    SecurityLevel,
    get_factory_level,
)

logger = structlog.get_logger(__name__)

# Fields the PATCH endpoint is allowed to mutate. Slug/name/icon/color are
# kept stable (UI keys on them); intensity is derived, not editable.
EDITABLE_FIELDS = {
    "description",
    "default_provider_slug",
    "allowed_provider_slugs",
    "adguard_filtering",
    "safe_browsing",
    "parental_control",
    "safe_search",
    "blocked_services",
    "adguard_blocklist_slugs",
    "require_dot",
    "require_dnssec",
    "eu_only",
}


def _row_to_dataclass(row: DnsSecurityLevelRow) -> SecurityLevel:
    """Materialize a DB row as the SecurityLevel dataclass used by the manager."""
    return SecurityLevel(
        slug=row.slug,  # type: ignore[arg-type]
        name=row.name,
        description=row.description,
        icon=row.icon,
        color=row.color,
        default_provider_slug=row.default_provider_slug,
        allowed_provider_slugs=list(row.allowed_provider_slugs or []),
        adguard_filtering=row.adguard_filtering,
        safe_browsing=row.safe_browsing,
        parental_control=row.parental_control,
        safe_search=row.safe_search,
        blocked_services=list(row.blocked_services or []),
        adguard_blocklist_slugs=list(row.adguard_blocklist_slugs or []),
        require_dot=row.require_dot,
        require_dnssec=row.require_dnssec,
        eu_only=row.eu_only,
        intensity=row.intensity,  # type: ignore[arg-type]
    )


def _dataclass_to_row_kwargs(level: SecurityLevel) -> dict[str, Any]:
    """Flatten a dataclass into kwargs suitable for a fresh DB row."""
    return {
        "slug": level.slug,
        "name": level.name,
        "description": level.description,
        "icon": level.icon,
        "color": level.color,
        "default_provider_slug": level.default_provider_slug,
        "allowed_provider_slugs": list(level.allowed_provider_slugs),
        "adguard_filtering": level.adguard_filtering,
        "safe_browsing": level.safe_browsing,
        "parental_control": level.parental_control,
        "safe_search": level.safe_search,
        "blocked_services": list(level.blocked_services),
        "adguard_blocklist_slugs": list(level.adguard_blocklist_slugs),
        "require_dot": level.require_dot,
        "require_dnssec": level.require_dnssec,
        "eu_only": level.eu_only,
        "intensity": level.intensity,
    }


class DnsSecurityLevelStore:
    """CRUD + seed for `dns_security_levels`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def ensure_seeded(self) -> int:
        """Insert any FACTORY_LEVELS row missing from the table. Returns inserted count.

        Idempotent. Existing rows are left untouched — user edits persist
        across restarts.
        """
        added = 0
        async with self._sf() as session:
            existing = await session.execute(select(DnsSecurityLevelRow.slug))
            existing_slugs = {s for (s,) in existing.all()}
            for level in FACTORY_LEVELS:
                if level.slug in existing_slugs:
                    continue
                session.add(DnsSecurityLevelRow(**_dataclass_to_row_kwargs(level)))
                added += 1
            if added:
                await session.commit()
        if added:
            logger.info("dns_security_levels.seeded", added=added)
        return added

    async def list_all(self) -> list[SecurityLevel]:
        async with self._sf() as session:
            r = await session.execute(
                select(DnsSecurityLevelRow).order_by(DnsSecurityLevelRow.slug)
            )
            return [_row_to_dataclass(row) for row in r.scalars().all()]

    async def get(self, slug: str) -> SecurityLevel | None:
        async with self._sf() as session:
            r = await session.execute(
                select(DnsSecurityLevelRow).where(DnsSecurityLevelRow.slug == slug)
            )
            row = r.scalar_one_or_none()
            return _row_to_dataclass(row) if row else None

    async def update(self, slug: str, patch: dict[str, Any]) -> SecurityLevel:
        """Apply a partial update to a level. Unknown fields are ignored.

        Validation (provider exists, slugs in catalog, etc.) is the caller's
        responsibility — see `validate_provider_for_level`.
        """
        # Filter to allowed fields only — silently drop anything else.
        clean = {k: v for k, v in patch.items() if k in EDITABLE_FIELDS}
        if not clean:
            existing = await self.get(slug)
            if existing is None:
                raise KeyError(slug)
            return existing
        async with self._sf() as session:
            r = await session.execute(
                select(DnsSecurityLevelRow).where(DnsSecurityLevelRow.slug == slug)
            )
            row = r.scalar_one_or_none()
            if row is None:
                raise KeyError(slug)
            for k, v in clean.items():
                setattr(row, k, v)
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return _row_to_dataclass(row)

    async def reset_to_factory(self, slug: str) -> SecurityLevel:
        """Overwrite the DB row with the FACTORY_LEVELS values. Atomic."""
        factory = get_factory_level(slug)
        if factory is None:
            raise KeyError(slug)
        async with self._sf() as session:
            r = await session.execute(
                select(DnsSecurityLevelRow).where(DnsSecurityLevelRow.slug == slug)
            )
            row = r.scalar_one_or_none()
            kw = _dataclass_to_row_kwargs(factory)
            if row is None:
                session.add(DnsSecurityLevelRow(**kw))
            else:
                for k, v in kw.items():
                    setattr(row, k, v)
                row.updated_at = datetime.now(UTC)
            await session.commit()
        logger.info("dns_security_levels.reset", slug=slug)
        return factory
