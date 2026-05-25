"""FastAPI dependency injection helpers.

Per-device runtime objects (SlateClient, SlateSSH, URL resolver,
AdGuardManager) come from `DeviceConnectionsRegistry` — a lazy
`slug → bundle` cache. The default-device shortcuts kept below
(`get_slate_client`, `get_slate_ssh`, …) resolve the default device
through the registry at request time. Routes that want to target a
specific device should take an optional `?device=<slug>` query param
and call `get_device_connections_for_slug` instead.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status

from app.adguard.manager import AdGuardManager
from app.devices.registry import (
    DeviceConnections,
    DeviceConnectionsRegistry,
    DeviceRegistryError,
)
from app.devices.store import DeviceStore
from app.dns.manager import DnsProtectionManager
from app.dns.store import DnsSecurityLevelStore
from app.networks.store import NetworkStore
from app.slate.url_resolver import SlateUrlResolver
from app.profiles.store import ProfileStore
from app.security.exploit_enricher import ExploitEnricher
from app.security.scanner import SecurityScanner
from app.security.store import SecurityStore
from app.settings.ssh_keys import SSHKeypairStore
from app.slate.client import SlateClient
from app.slate.profiles import ProfileManager
from app.slate.ssh import SlateSSH
from app.vpn.configs_store import VPNConfigStore
from app.vpn.proton_client import ProtonClient
from app.wifi.store import WifiSsidStore


def get_device_registry(request: Request) -> DeviceConnectionsRegistry:
    """Return the per-device connection registry bound to the lifespan."""
    return request.app.state.device_registry


async def get_device_connections(
    request: Request,
    device: Annotated[
        str | None,
        Query(
            description=(
                "Optional device slug. Omit to use the default device. "
                "Use this to target a non-default device without "
                "restarting the controller."
            ),
        ),
    ] = None,
) -> DeviceConnections:
    """Resolve a `DeviceConnections` bundle from the optional ?device= query.

    Default = the device with `is_default=True`. Wraps registry errors
    into a clean HTTP response so callers see 404/503 rather than 500.
    """
    registry: DeviceConnectionsRegistry = request.app.state.device_registry
    try:
        if device is None:
            return await registry.for_default()
        return await registry.for_slug(device)
    except DeviceRegistryError as exc:
        # "unknown device" → 404 ; "no device registered" / "no creds" → 503.
        msg = str(exc)
        if "unknown device" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg,
        ) from exc


async def get_slate_client(
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
) -> SlateClient:
    """Return the `SlateClient` for the requested device (default if omitted).

    Same name + position as the pre-multi-device helper, so every existing
    `Depends(get_slate_client)` keeps working without changes.
    """
    return conn.client


async def get_slate_ssh(
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
) -> SlateSSH:
    """Return the `SlateSSH` channel for the requested device."""
    return conn.ssh


def get_profile_manager(request: Request) -> ProfileManager:
    """Return the singleton `ProfileManager` (YAML seed loader) — internal use."""
    manager: ProfileManager = request.app.state.profile_manager
    return manager


def get_profile_store(request: Request) -> ProfileStore:
    """Return the DB-backed `ProfileStore` — public API surface."""
    store: ProfileStore = request.app.state.profile_store
    return store


def get_proton_client(request: Request) -> ProtonClient:
    """Return the singleton `ProtonClient` bound to the application lifespan."""
    client: ProtonClient = request.app.state.proton_client
    return client


def get_vpn_config_store(request: Request) -> VPNConfigStore:
    """Return the singleton `VPNConfigStore` bound to the application lifespan."""
    store: VPNConfigStore = request.app.state.vpn_config_store
    return store


def get_wifi_store(request: Request) -> WifiSsidStore:
    """Return the singleton `WifiSsidStore` bound to the application lifespan."""
    store: WifiSsidStore = request.app.state.wifi_store
    return store


def get_network_store(request: Request) -> NetworkStore:
    """Return the singleton `NetworkStore` bound to the application lifespan."""
    store: NetworkStore = request.app.state.network_store
    return store


def get_device_store(request: Request) -> DeviceStore:
    """Return the singleton `DeviceStore` bound to the application lifespan."""
    store: DeviceStore = request.app.state.device_store
    return store


def get_ssh_keypair_store(request: Request) -> SSHKeypairStore:
    """Return the singleton `SSHKeypairStore` bound to the application lifespan."""
    store: SSHKeypairStore = request.app.state.ssh_keypair_store
    return store


async def get_adguard_manager(
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
) -> AdGuardManager:
    """Return the `AdGuardManager` bound to the requested device's SSH channel."""
    return conn.adguard


def get_security_store(request: Request) -> SecurityStore:
    """Return the singleton `SecurityStore` bound to the application lifespan."""
    store: SecurityStore = request.app.state.security_store
    return store


def get_security_scanner(request: Request) -> SecurityScanner:
    """Return the singleton `SecurityScanner` bound to the application lifespan."""
    scanner: SecurityScanner = request.app.state.security_scanner
    return scanner


def get_exploit_enricher(request: Request) -> ExploitEnricher:
    """Return the singleton `ExploitEnricher` bound to the application lifespan."""
    enricher: ExploitEnricher = request.app.state.exploit_enricher
    return enricher


def get_dns_protection_manager(request: Request) -> DnsProtectionManager:
    """Return the singleton `DnsProtectionManager` bound to the application lifespan."""
    manager: DnsProtectionManager = request.app.state.dns_protection_manager
    return manager


def get_dns_security_level_store(request: Request) -> DnsSecurityLevelStore:
    """Return the singleton `DnsSecurityLevelStore` bound to the application lifespan."""
    store: DnsSecurityLevelStore = request.app.state.dns_security_level_store
    return store


async def get_slate_url_resolver(
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
) -> SlateUrlResolver:
    """Return the URL resolver for the requested device."""
    return conn.url_resolver
