"""Per-device runtime objects (SlateClient, SlateSSH, URL resolver,
AdGuard manager), lazy-built and cached by slug.

Why this exists
---------------
Before this registry, the controller built a SINGLE SlateClient/SlateSSH
pair in the lifespan, hard-bound to the default device. To work with a
different device the user had to mark it as default *and* restart the
backend — friction that blocked real multi-device usage even though the
DB layer (devices + device_secrets) had supported it since day one.

The registry replaces the singletons with a (slug → DeviceConnections)
cache. Existing routes that don't care about the device transparently
get the default-device connections via the unchanged DI helpers
(`get_slate_client`, `get_slate_ssh`, …). Routes that want to target a
specific device take an optional `?device=slug` query param and call
`get_device_connections_for_slug(slug)` instead.

Invalidation
------------
Whenever a device's admin URLs or credentials change (PATCH /devices),
the registry's cached entry for that slug is dropped + the old SSH
connection is closed gracefully. The next access rebuilds with fresh
state. Adoption tasks that need a freshly-credentialed connection
should call `invalidate(slug)` after writing credentials before issuing
new SSH commands.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from app.adguard.manager import AdGuardManager
from app.config import Settings, get_settings
from app.devices.store import DeviceStore
from app.exceptions import SlateError
from app.settings.ssh_keys import SSHKeypairStore
from app.slate.client import SlateClient
from app.slate.ssh import SlateSSH
from app.slate.url_resolver import SlateUrlResolver

logger = structlog.get_logger(__name__)


class DeviceRegistryError(SlateError):
    """Raised when a device slug can't be resolved or is mis-configured."""


@dataclass
class DeviceConnections:
    """Bundle of every "talks to this Slate" object, scoped to one device.

    Lifetime : created lazily on first access for a slug, kept until the
    device is updated/deleted (then invalidated). Multiple requests for
    the same slug share the same bundle — they're internally serialized
    by asyncio locks held by SlateClient / SlateSSH.
    """

    slug: str
    client: SlateClient
    ssh: SlateSSH
    url_resolver: SlateUrlResolver
    adguard: AdGuardManager

    async def aclose(self) -> None:
        """Best-effort teardown — never raises."""
        try:
            await self.ssh.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must not propagate
            logger.warning(
                "device_connections.ssh_close_failed",
                slug=self.slug, error=str(exc),
            )
        try:
            await self.client.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "device_connections.client_disconnect_failed",
                slug=self.slug, error=str(exc),
            )


def _default_url_for(host: str, rpc_scheme: str, rpc_port: int) -> str:
    """Build a fallback admin URL from a device row's bare fields."""
    if rpc_port not in (80, 443):
        return f"{rpc_scheme}://{host}:{rpc_port}"
    return f"{rpc_scheme}://{host}"


class DeviceConnectionsRegistry:
    """Lazy cache of `DeviceConnections` keyed by device slug.

    Thread-safety : a single asyncio lock serializes builds. Cache reads
    are lockless after the first successful build — they hit the dict.
    The trade-off is a brief lock contention during cold-start for a
    given slug, which is fine for a controller-sized workload.
    """

    def __init__(
        self,
        *,
        device_store: DeviceStore,
        ssh_keypair_store: SSHKeypairStore,
        settings: Settings | None = None,
    ) -> None:
        self._device_store = device_store
        self._ssh_keypair_store = ssh_keypair_store
        self._settings = settings or get_settings()
        self._cache: dict[str, DeviceConnections] = {}
        self._build_lock = asyncio.Lock()

    async def for_slug(self, slug: str) -> DeviceConnections:
        """Return the bundle for `slug`, building it on cache miss.

        Raises `DeviceRegistryError` when the slug doesn't exist or the
        device has no stored credentials.
        """
        cached = self._cache.get(slug)
        if cached is not None:
            return cached
        async with self._build_lock:
            # Double-check inside the lock — another coroutine may have
            # built it while we were waiting.
            cached = self._cache.get(slug)
            if cached is not None:
                return cached
            conn = await self._build(slug)
            self._cache[slug] = conn
            return conn

    async def for_default(self) -> DeviceConnections:
        """Return the bundle for the device flagged `is_default=True`.

        Falls back to the lowest-id device if no default is set. Raises
        if no devices exist at all.
        """
        row = await self._device_store.get_default()
        if row is None:
            rows = await self._device_store.list_all()
            if not rows:
                raise DeviceRegistryError(
                    "no device registered — adopt a Slate first",
                )
            row = rows[0]
        return await self.for_slug(row.slug)

    async def invalidate(self, slug: str) -> None:
        """Drop the cached bundle for `slug` and close its connections.

        Idempotent — no-op if nothing was cached. Call after editing a
        device's admin_urls / credentials, or after deleting it.
        """
        async with self._build_lock:
            conn = self._cache.pop(slug, None)
        if conn is None:
            return
        await conn.aclose()
        logger.info("device_registry.invalidated", slug=slug)

    async def aclose_all(self) -> None:
        """Lifespan-end teardown. Best-effort, never raises."""
        async with self._build_lock:
            conns = list(self._cache.values())
            self._cache.clear()
        for c in conns:
            await c.aclose()

    # ---------------------------- build ---------------------------- #

    async def _build(self, slug: str) -> DeviceConnections:
        row = await self._device_store.get_by_slug(slug)
        if row is None:
            raise DeviceRegistryError(f"unknown device {slug!r}")
        creds = await self._device_store.get_rpc_credentials(slug)
        if creds is None:
            raise DeviceRegistryError(
                f"device {slug!r} has no stored RPC credentials",
            )
        username, password = creds

        # admin_urls → resolver candidates. Backfill from `host` for legacy
        # rows that haven't been edited since admin_urls landed (mirrors
        # _to_public() in routes/devices.py).
        admin_urls = list(row.admin_urls or [])
        if not admin_urls and row.host:
            admin_urls = [_default_url_for(row.host, row.rpc_scheme, row.rpc_port)]
        if not admin_urls:
            raise DeviceRegistryError(
                f"device {slug!r} has no admin_urls and no host",
            )

        resolver = SlateUrlResolver(
            urls=admin_urls,
            probe_port=row.ssh_port or 22,
        )
        # Probe immediately so the resolver's `active_url` reflects
        # reality before we build SlateSSH / AdGuardManager — both
        # snapshot `resolver.active_url` at construction time for their
        # static fields (e.g. AdGuard's `base_url` host). Without this
        # they'd freeze on `urls[0]` even if it's down.
        await resolver.force_refresh()
        # Use the freshly-probed active URL as the static fallback inside
        # SlateClient/SlateSSH — the resolver overrides this on each call.
        initial_url = resolver.active_url

        client = SlateClient(
            url=initial_url,
            username=username,
            password=password,
            url_resolver=resolver,
        )
        ssh = SlateSSH(
            slate_url=initial_url,
            username=username,
            password=password,
            port=row.ssh_port or 22,
            url_resolver=resolver,
        )

        # If the device has a deployed SSH keypair, switch to key auth so
        # we don't fall back to password (which is OFF on hardened devices).
        try:
            status = await self._ssh_keypair_store.get_status(slug)
            if status.generated and status.deployed_to_slate:
                pem = await self._ssh_keypair_store.get_private_pem(slug)
                if pem:
                    await ssh.use_private_key(pem)
                    logger.info(
                        "device_registry.using_stored_keypair", slug=slug,
                    )
        except Exception as exc:  # noqa: BLE001 - bootstrap must keep going
            logger.warning(
                "device_registry.keypair_load_failed",
                slug=slug, error=str(exc),
            )

        # AdGuard manager bound to this device's SSH/host. The admin
        # credentials are global (controller's admin) — that's by design:
        # we reuse the controller's admin password for AdGuard's REST UI
        # too, so the user has one password to remember.
        adguard = AdGuardManager(
            ssh=ssh,
            slate_host=ssh.host,
            admin_username=self._settings.admin_username,
            admin_password=self._settings.admin_password,
        )

        logger.info(
            "device_registry.built",
            slug=slug, admin_urls=admin_urls,
            ssh_auth_mode=ssh.auth_mode,
        )
        return DeviceConnections(
            slug=slug, client=client, ssh=ssh,
            url_resolver=resolver, adguard=adguard,
        )
