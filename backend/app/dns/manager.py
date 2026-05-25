"""Apply DNS security levels to networks via AdGuard Home Clients API.

For each `(network_slug, level_slug, provider_slug)` triple stored in DB,
we create / update a persistent client in AdGuard Home identified by the
network's CIDR, configured with the upstream + filtering rules dictated by
the security level.

AdGuard's client routing is by source IP: when a query comes in from an IP
matching a persistent client's `ids` (CIDR/IP/MAC/ClientID), that client's
config takes precedence over the global one. So a single AdGuard instance
serves all our networks with different policies.

The manager is the single writer of those clients (identified by the
`[slate-ctrl-net]` name prefix). Clients without that prefix are left alone
— matches the same "don't trample what the operator did manually" contract
as the agent's adguard.sh handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adguard.manager import AdGuardError, AdGuardManager
from app.db.models import NetworkDnsProtectionRow
from app.dns.catalog import DnsProvider, get_provider
from app.dns.security_levels import (
    SecurityLevel,
    validate_provider_for_level,
)
from app.dns.store import DnsSecurityLevelStore
from app.exceptions import SlateError
from app.networks.store import NetworkNotFoundError, NetworkStore

logger = structlog.get_logger(__name__)

# Name prefix marking AdGuard clients we own. Same idea as the
# `[slate-ctrl]` marker on filter lists — distinguishes managed vs.
# operator-added clients so we never touch the latter.
CLIENT_NAME_PREFIX = "[slate-ctrl-net] "


class DnsProtectionError(SlateError):
    """Raised when something goes wrong applying a DNS protection."""


@dataclass
class NetworkProtection:
    """Materialized view: network + level + effective provider."""

    network_slug: str
    network_display_name: str
    network_cidr: str
    level_slug: str
    level_name: str
    provider_slug: str  # effective (may be override or level default)
    provider_name: str
    provider_country: str
    provider_eu_based: bool
    provider_filter_profile: str
    upstream_transports: list[str]  # ["DoT", "DoH"] etc — for UI badges
    adguard_client_name: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ApplyReport:
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class DnsProtectionStore:
    """DB-level CRUD for the network_dns_protection table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_all(self) -> list[NetworkDnsProtectionRow]:
        async with self._sf() as session:
            r = await session.execute(select(NetworkDnsProtectionRow))
            return list(r.scalars().all())

    async def get(self, network_slug: str) -> NetworkDnsProtectionRow | None:
        async with self._sf() as session:
            r = await session.execute(
                select(NetworkDnsProtectionRow).where(
                    NetworkDnsProtectionRow.network_slug == network_slug
                )
            )
            return r.scalar_one_or_none()

    async def upsert(
        self,
        network_slug: str,
        *,
        level_slug: str,
        provider_slug: str | None,
        adguard_client_name: str,
    ) -> NetworkDnsProtectionRow:
        async with self._sf() as session:
            r = await session.execute(
                select(NetworkDnsProtectionRow).where(
                    NetworkDnsProtectionRow.network_slug == network_slug
                )
            )
            row = r.scalar_one_or_none()
            now = datetime.now(UTC)
            if row is None:
                row = NetworkDnsProtectionRow(
                    network_slug=network_slug,
                    level_slug=level_slug,
                    provider_slug=provider_slug,
                    adguard_client_name=adguard_client_name,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.level_slug = level_slug
                row.provider_slug = provider_slug
                row.adguard_client_name = adguard_client_name
                row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def delete(self, network_slug: str) -> bool:
        async with self._sf() as session:
            r = await session.execute(
                delete(NetworkDnsProtectionRow).where(
                    NetworkDnsProtectionRow.network_slug == network_slug
                )
            )
            await session.commit()
            return r.rowcount > 0


class DnsProtectionManager:
    """High-level orchestration. Wraps `DnsProtectionStore` + `AdGuardManager`.

    Levels are looked up from `DnsSecurityLevelStore` (DB-backed, editable)
    rather than the Python `FACTORY_LEVELS` const — that way user edits to
    a level (e.g. changing its default provider) take effect immediately on
    the next apply.
    """

    def __init__(
        self,
        *,
        store: DnsProtectionStore,
        networks: NetworkStore,
        adguard: AdGuardManager,
        levels: DnsSecurityLevelStore,
    ) -> None:
        self._store = store
        self._networks = networks
        self._adguard = adguard
        self._levels = levels

    # ---------------------------- public API ---------------------------- #

    async def list_protections(self) -> list[NetworkProtection]:
        """Return one materialized view per persisted mapping."""
        rows = await self._store.list_all()
        out: list[NetworkProtection] = []
        for row in rows:
            try:
                network = await self._networks.get(row.network_slug)
            except NetworkNotFoundError:
                # Network was deleted but the protection row lingered. Surface
                # so the UI can offer cleanup.
                continue
            level = await self._levels.get(row.level_slug)
            if level is None:
                continue
            effective_provider_slug = row.provider_slug or level.default_provider_slug
            provider = get_provider(effective_provider_slug)
            if provider is None:
                continue
            out.append(
                self._build_view(
                    network_slug=row.network_slug,
                    network_display_name=network.display_name,
                    network_cidr=network.subnet_cidr,
                    level=level,
                    provider=provider,
                    adguard_client_name=row.adguard_client_name,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return out

    async def get_protection(self, network_slug: str) -> NetworkProtection | None:
        row = await self._store.get(network_slug)
        if row is None:
            return None
        network = await self._networks.get(network_slug)
        level = await self._levels.get(row.level_slug)
        if level is None:
            raise DnsProtectionError(
                f"row references unknown level '{row.level_slug}'"
            )
        provider = get_provider(row.provider_slug or level.default_provider_slug)
        if provider is None:
            raise DnsProtectionError(
                f"row references unknown provider '{row.provider_slug}'"
            )
        return self._build_view(
            network_slug=network_slug,
            network_display_name=network.display_name,
            network_cidr=network.subnet_cidr,
            level=level,
            provider=provider,
            adguard_client_name=row.adguard_client_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def set_protection(
        self,
        network_slug: str,
        *,
        level_slug: str,
        provider_slug: str | None = None,
    ) -> NetworkProtection:
        """Persist + apply to AdGuard.

        `provider_slug=None` → use the level's default. Otherwise must be in
        the level's allowed list (`validate_provider_for_level`).
        """
        network = await self._networks.get(network_slug)
        level = await self._levels.get(level_slug)
        if level is None:
            raise DnsProtectionError(f"unknown security level: {level_slug!r}")

        effective = provider_slug or level.default_provider_slug
        err = validate_provider_for_level(level, effective)
        if err is not None:
            raise DnsProtectionError(err)
        provider = get_provider(effective)
        assert provider is not None  # validated above

        client_name = self._make_client_name(network.slug, network.display_name)
        payload = self._build_client_payload(
            client_name=client_name,
            cidr=network.subnet_cidr,
            level=level,
            provider=provider,
        )

        # 1. Apply on AdGuard side (add or update). We must know if a client
        # with this name already exists; the API distinguishes add vs update.
        await self._apply_adguard_client(client_name=client_name, payload=payload)

        # 2. Persist on DB side. We persist AFTER successful apply so a
        # failed apply doesn't leave the DB out of sync.
        row = await self._store.upsert(
            network_slug=network_slug,
            level_slug=level_slug,
            provider_slug=provider_slug,
            adguard_client_name=client_name,
        )
        logger.info(
            "dns_protection.applied",
            network=network_slug,
            level=level_slug,
            provider=effective,
        )
        return self._build_view(
            network_slug=network_slug,
            network_display_name=network.display_name,
            network_cidr=network.subnet_cidr,
            level=level,
            provider=provider,
            adguard_client_name=row.adguard_client_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def remove_protection(self, network_slug: str) -> None:
        """Delete the DB row AND the AdGuard client."""
        row = await self._store.get(network_slug)
        if row is None:
            return
        # Try to delete on AdGuard first; if AdGuard is down, we still drop
        # the DB row to avoid getting stuck in a "phantom protection" state.
        if row.adguard_client_name:
            try:
                await self._delete_adguard_client(row.adguard_client_name)
            except AdGuardError as exc:
                logger.warning(
                    "dns_protection.adguard_delete_failed",
                    network=network_slug,
                    error=str(exc),
                )
        await self._store.delete(network_slug)
        logger.info("dns_protection.removed", network=network_slug)

    async def reapply_all(self) -> ApplyReport:
        """Re-push every stored protection to AdGuard.

        Use after AdGuard restart / fresh bootstrap / state restore: we
        don't trust that AdGuard's clients survived. Idempotent.
        """
        rep = ApplyReport()
        rows = await self._store.list_all()
        for row in rows:
            try:
                network = await self._networks.get(row.network_slug)
            except NetworkNotFoundError:
                rep.skipped.append(f"{row.network_slug} (network deleted)")
                continue
            level = await self._levels.get(row.level_slug)
            if level is None:
                rep.errors.append(f"{row.network_slug}: unknown level {row.level_slug!r}")
                continue
            provider = get_provider(row.provider_slug or level.default_provider_slug)
            if provider is None:
                rep.errors.append(
                    f"{row.network_slug}: unknown provider "
                    f"{row.provider_slug or level.default_provider_slug!r}"
                )
                continue
            client_name = row.adguard_client_name or self._make_client_name(
                network.slug, network.display_name
            )
            payload = self._build_client_payload(
                client_name=client_name, cidr=network.subnet_cidr,
                level=level, provider=provider,
            )
            try:
                await self._apply_adguard_client(client_name=client_name, payload=payload)
                rep.applied.append(row.network_slug)
            except AdGuardError as exc:
                rep.errors.append(f"{row.network_slug}: {exc}")
        return rep

    # ---------------------------- internals ---------------------------- #

    def _make_client_name(self, slug: str, display_name: str) -> str:
        """Build the AdGuard client name with our marker prefix."""
        # Short, distinctive, safe for AdGuard's display.
        return f"{CLIENT_NAME_PREFIX}{display_name} ({slug})"

    def _build_view(
        self,
        *,
        network_slug: str,
        network_display_name: str,
        network_cidr: str,
        level: SecurityLevel,
        provider: DnsProvider,
        adguard_client_name: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> NetworkProtection:
        transports: list[str] = []
        if provider.dot_hostname:
            transports.append("DoT")
        if provider.doh_url:
            transports.append("DoH")
        if not transports:
            transports.append("UDP")
        return NetworkProtection(
            network_slug=network_slug,
            network_display_name=network_display_name,
            network_cidr=network_cidr,
            level_slug=level.slug,
            level_name=level.name,
            provider_slug=provider.slug,
            provider_name=provider.name,
            provider_country=provider.country,
            provider_eu_based=provider.is_eu_based,
            provider_filter_profile=provider.filter_profile,
            upstream_transports=transports,
            adguard_client_name=adguard_client_name,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _build_upstreams(self, provider: DnsProvider) -> list[str]:
        """Build AdGuard's `upstreams` list, encrypted first.

        AdGuard tries upstreams in order: if the first responds, that's
        the one used. Putting DoT/DoH first means encrypted is preferred,
        with plain UDP as a degraded fallback.
        """
        urls: list[str] = []
        if provider.dot_hostname:
            urls.append(f"tls://{provider.dot_hostname}:{provider.dot_port}")
        if provider.doh_url:
            urls.append(provider.doh_url)
        # Plain UDP fallback. Important: keep, because if DoT/DoH fails
        # AdGuard otherwise has nothing.
        if provider.ipv4_primary:
            urls.append(provider.ipv4_primary)
        if provider.ipv4_secondary:
            urls.append(provider.ipv4_secondary)
        return urls

    def _build_client_payload(
        self,
        *,
        client_name: str,
        cidr: str,
        level: SecurityLevel,
        provider: DnsProvider,
    ) -> dict[str, Any]:
        """Translate a security level into an AdGuard `Client` payload."""
        # NOTE: no `tags` — AdGuard rejects unknown tag names with HTTP 400
        # ("invalid tag"). Only its built-in set (device_*, user_*, os_*) is
        # allowed. The marker prefix on `name` is enough to identify our
        # clients on the next reconciliation pass.
        return {
            "name": client_name,
            "ids": [cidr],
            "use_global_settings": False,
            "filtering_enabled": level.adguard_filtering,
            "parental_enabled": level.parental_control,
            "safebrowsing_enabled": level.safe_browsing,
            "safe_search": {"enabled": level.safe_search},
            "use_global_blocked_services": False,
            "blocked_services": list(level.blocked_services),
            "upstreams": self._build_upstreams(provider),
            "ignore_querylog": False,
            "ignore_statistics": False,
        }

    async def _adguard_get(self, path: str) -> Any:
        try:
            resp = await self._adguard._http.get(path)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard GET {path} failed: {exc}") from exc

    async def _adguard_post(self, path: str, body: dict[str, Any]) -> None:
        try:
            resp = await self._adguard._http.post(path, json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard POST {path} failed: {exc}") from exc

    async def _client_exists(self, client_name: str) -> bool:
        data = await self._adguard_get("/control/clients")
        for c in data.get("clients") or []:
            if c.get("name") == client_name:
                return True
        return False

    async def _apply_adguard_client(
        self, *, client_name: str, payload: dict[str, Any]
    ) -> None:
        """Add or update a persistent client. Idempotent."""
        if await self._client_exists(client_name):
            # update payload shape: {name: <existing>, data: <new fields>}
            await self._adguard_post(
                "/control/clients/update",
                {"name": client_name, "data": payload},
            )
        else:
            await self._adguard_post("/control/clients/add", payload)

    async def _delete_adguard_client(self, client_name: str) -> None:
        if not await self._client_exists(client_name):
            return
        await self._adguard_post(
            "/control/clients/delete", {"name": client_name}
        )


# `list_levels()` removed — the API route now reads from `DnsSecurityLevelStore`
# directly so user edits are reflected. The Python `FACTORY_LEVELS` const is
# only used by the store's seed + reset paths.

# Also add a helper for the route to reapply all protections that use a given
# level — needed after an edit so the new config propagates to AdGuard.
async def reapply_protections_using_level(
    *, manager: DnsProtectionManager, level_slug: str,
) -> ApplyReport:
    """Re-push to AdGuard every protection bound to `level_slug`.

    Called by the PATCH /security-levels/{slug} endpoint after an edit, so
    the new upstream/blocklists/toggles take effect immediately on every
    network using this level.
    """
    rep = ApplyReport()
    rows = await manager._store.list_all()
    for row in rows:
        if row.level_slug != level_slug:
            continue
        try:
            await manager.set_protection(
                row.network_slug,
                level_slug=row.level_slug,
                provider_slug=row.provider_slug,
            )
            rep.applied.append(row.network_slug)
        except (AdGuardError, NetworkNotFoundError, DnsProtectionError) as exc:
            rep.errors.append(f"{row.network_slug}: {exc}")
    return rep
