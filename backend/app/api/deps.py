"""FastAPI dependency injection helpers."""

from __future__ import annotations

from fastapi import Request

from app.adguard.manager import AdGuardManager
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


def get_slate_client(request: Request) -> SlateClient:
    """Return the singleton `SlateClient` bound to the application lifespan."""
    client: SlateClient = request.app.state.slate_client
    return client


def get_slate_ssh(request: Request) -> SlateSSH:
    """Return the singleton `SlateSSH` bound to the application lifespan."""
    ssh: SlateSSH = request.app.state.slate_ssh
    return ssh


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


def get_ssh_keypair_store(request: Request) -> SSHKeypairStore:
    """Return the singleton `SSHKeypairStore` bound to the application lifespan."""
    store: SSHKeypairStore = request.app.state.ssh_keypair_store
    return store


def get_adguard_manager(request: Request) -> AdGuardManager:
    """Return the singleton `AdGuardManager` bound to the application lifespan."""
    manager: AdGuardManager = request.app.state.adguard_manager
    return manager


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


def get_slate_url_resolver(request: Request) -> SlateUrlResolver:
    """Return the singleton `SlateUrlResolver` bound to the application lifespan."""
    resolver: SlateUrlResolver = request.app.state.slate_url_resolver
    return resolver
