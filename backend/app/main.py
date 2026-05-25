"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.adguard.manager import AdGuardManager
from app.api.routes import adguard as adguard_routes
from app.api.routes import agent as agent_routes
from app.api.routes import auth as auth_routes
from app.api.routes import devices as device_routes
from app.api.routes import dns_protection as dns_protection_routes
from app.api.routes import networks as network_routes
from app.api.routes import profiles as profile_routes
from app.api.routes import proton as proton_routes
from app.api.routes import security as security_routes
from app.api.routes import settings as settings_routes
from app.api.routes import slate as slate_routes
from app.api.routes import tailscale as tailscale_routes
from app.api.routes import vpn_configs as vpn_config_routes
from app.api.routes import wifi as wifi_routes
from app.config import get_settings
from app.db.database import init_db, make_engine, make_session_factory
from app.devices.store import DeviceStore
from app.networks.models import NetworkCreate
from app.networks.store import NetworkStore
from app.profiles.store import ProfileStore
from app.settings.ssh_keys import SSHKeypairStore
from app.slate.client import SlateClient
from app.slate.profiles import ProfileManager
from app.slate.ssh import SlateSSH
from app.vpn.configs_store import VPNConfigStore
from app.vpn.proton_client import ProtonClient
from app.wifi.models import WifiSsidCreate
from app.wifi.store import WifiSsidStore

# Built-in networks — mirror the Slate's stock bridges. Editable but not deletable.
DEFAULT_NETWORKS: list[NetworkCreate] = [
    # 5 zones, slug = SSID name = network name (1:1 mapping for clarity).
    # Random RFC1918 offsets (option C, no common base) to minimise conflict
    # risk in hotel/coworking environments.
    NetworkCreate(
        slug="neuralcore",
        display_name="NeuralCore (LAN principal perso)",
        bridge_name="br-neuralcore",
        subnet_cidr="10.137.42.0/24",
        gateway_ip="10.137.42.1",
        dhcp_enabled=True,
        isolated_from_lan=False,
        ipv6_enabled=True,
        ipv6_subnet_cidr="fd5a:6c14:e23b:8::/64",
        notes="Zone trusted: NAS, imprimante, AirPlay, smart home perso.",
    ),
    NetworkCreate(
        slug="grid",
        display_name="Grid (kids)",
        bridge_name="br-grid",
        subnet_cidr="10.91.18.0/24",
        gateway_ip="10.91.18.1",
        dhcp_enabled=True,
        isolated_from_lan=True,
        ipv6_enabled=True,
        ipv6_subnet_cidr="fd5a:6c14:e23b:9::/64",
        notes="Zone enfants. REJECT vers neuralcore/blackice. AdGuard strict.",
    ),
    NetworkCreate(
        slug="blackice",
        display_name="BlackIce (mission corporate)",
        bridge_name="br-blackice",
        subnet_cidr="10.204.5.0/24",
        gateway_ip="10.204.5.1",
        dhcp_enabled=True,
        isolated_from_lan=True,
        ipv6_enabled=True,
        ipv6_subnet_cidr="fd5a:6c14:e23b:10::/64",
        notes="Zone mission corporate. VPN forcé, REJECT vers neuralcore.",
    ),
    NetworkCreate(
        slug="chromelounge",
        display_name="ChromeLounge (invités)",
        bridge_name="br-chromelounge",
        subnet_cidr="10.66.211.0/24",
        gateway_ip="10.66.211.1",
        dhcp_enabled=True,
        isolated_from_lan=True,
        ipv6_enabled=True,
        ipv6_subnet_cidr="fd5a:6c14:e23b:20::/64",
        notes="Zone untrusted invités. Client iso ON, REJECT vers tout LAN.",
    ),
    NetworkCreate(
        slug="shadowrun",
        display_name="Shadowrun (burner OSINT)",
        bridge_name="br-shadowrun",
        subnet_cidr="10.183.7.0/24",
        gateway_ip="10.183.7.1",
        dhcp_enabled=True,
        isolated_from_lan=True,
        ipv6_enabled=True,
        ipv6_subnet_cidr="fd5a:6c14:e23b:21::/64",
        notes="Zone burner OSINT, séparée L2 de chromelounge (pas de bridge partagé).",
    ),
]

# Default Wi-Fi catalog. Slug == network slug == broadcast theme (1:1).
DEFAULT_WIFI_SSIDS: list[WifiSsidCreate] = [
    WifiSsidCreate(
        slug="neuralcore",
        ssid_name="NEURAL_LINK_01",
        band="MLO",
        security="WPA3-SAE",
        network_slug="neuralcore",
        client_isolation=False,
        notes="SSID principal perso, WiFi 7 MLO max perf, AirPlay/Chromecast OK",
    ),
    WifiSsidCreate(
        slug="grid",
        ssid_name="TRON_LEGACY",
        band="5GHz",
        security="WPA3-SAE",
        network_slug="grid",
        client_isolation=False,
        notes="Devices enfants. Iso OFF pour local play (Switch/jeux LAN)",
    ),
    WifiSsidCreate(
        slug="blackice",
        ssid_name="BLACK_ICE",
        band="MLO",
        security="WPA3-SAE",
        network_slug="blackice",
        client_isolation=True,
        notes="Mission corp. WPA3-only, client iso ON (defense in depth)",
    ),
    WifiSsidCreate(
        slug="chromelounge",
        ssid_name="CHROME_LOUNGE",
        band="2GHz",
        security="WPA2-PSK",
        network_slug="chromelounge",
        client_isolation=True,
        notes="Invités. WPA2 pour compat, client iso ON",
    ),
    WifiSsidCreate(
        slug="shadowrun",
        ssid_name="SHADOWRUN_NET",
        band="5GHz",
        security="WPA3-SAE",
        network_slug="shadowrun",
        client_isolation=True,
        notes="Burner OSINT, network propre (séparé L2 de chromelounge)",
    ),
]

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    settings = get_settings()
    logger.info("slate_controller.starting", version=__version__, slate_url=settings.slate_url)

    app.state.profile_manager = ProfileManager(settings.profiles_dir)
    app.state.proton_client = ProtonClient()

    # DB setup — creates `data/db/slate.db` and applies schema if needed.
    engine = make_engine()
    await init_db(engine)
    session_factory = make_session_factory(engine)
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.vpn_config_store = VPNConfigStore(session_factory)

    # Device store + first-boot migration from .env. The "default" device
    # backs the singleton slate_client / slate_ssh / adguard_manager that
    # most routes still use today. Multi-device active selection comes
    # later — for now we always operate on the default device.
    device_store = DeviceStore(session_factory)
    app.state.device_store = device_store
    default_device = await device_store.get_default()
    if default_device is None:
        # No device in DB yet → seed one from the .env values.
        from urllib.parse import urlparse

        parsed = urlparse(settings.slate_url)
        host = parsed.hostname or settings.slate_url
        scheme = parsed.scheme or "https"
        port = parsed.port or (443 if scheme == "https" else 80)
        default_device = await device_store.create(
            slug="slate",
            label="Slate 7 Pro (env)",
            model="slate-7-pro",
            host=host,
            rpc_port=port,
            rpc_scheme=scheme,
            ssh_port=22,
            rpc_username=settings.slate_username,
            rpc_password=settings.slate_password,
            notes="Auto-créé depuis .env au premier boot — renomme/édite si besoin.",
            is_default=True,
        )
        logger.info("devices.seeded_from_env", slug=default_device.slug, host=host)

    creds = await device_store.get_rpc_credentials(default_device.slug)
    if creds is None:
        raise RuntimeError(
            f"default device {default_device.slug!r} has no stored credentials",
        )
    rpc_username, rpc_password = creds
    rpc_url = (
        f"{default_device.rpc_scheme}://{default_device.host}:"
        f"{default_device.rpc_port}/rpc"
        if default_device.rpc_port not in (80, 443)
        else f"{default_device.rpc_scheme}://{default_device.host}/rpc"
    )

    # URL resolver: automatic LAN ↔ Tailscale ↔ custom failover. Seeded
    # from the default device's `admin_urls`. If empty (legacy row), fall
    # back to the single `rpc_url` derived from `host` above. The resolver
    # probes port 22 (SSH) on each candidate to decide which is reachable.
    from app.slate.url_resolver import SlateUrlResolver

    admin_urls = list(default_device.admin_urls or [])
    if not admin_urls:
        admin_urls = [rpc_url]
    app.state.slate_url_resolver = SlateUrlResolver(
        urls=admin_urls,
        probe_port=default_device.ssh_port or 22,
        probe_timeout=2.0,
        cache_ttl=10.0,
    )
    # Initial probe to populate the active URL — without it, the first
    # SSH call always uses urls[0] regardless of reachability.
    await app.state.slate_url_resolver.force_refresh()

    # Singleton SlateClient + SlateSSH — bound to the default device. Both
    # consume the URL resolver for transparent LAN ↔ Tailscale ↔ custom
    # failover on each (re)connect.
    app.state.slate_client = SlateClient(
        url=rpc_url,
        username=rpc_username,
        password=rpc_password,
        url_resolver=app.state.slate_url_resolver,
    )
    app.state.slate_ssh = SlateSSH(
        slate_url=rpc_url,  # fallback if resolver fails
        username=rpc_username,
        password=rpc_password,
        port=default_device.ssh_port,
        url_resolver=app.state.slate_url_resolver,
    )

    # Networks catalog (must seed BEFORE wifi — SSIDs reference network slugs).
    network_store = NetworkStore(session_factory)
    app.state.network_store = network_store
    await network_store.seed_builtins(DEFAULT_NETWORKS)

    # Wi-Fi catalog (seed defaults — profiles reference these slugs).
    wifi_store = WifiSsidStore(session_factory)
    app.state.wifi_store = wifi_store
    await wifi_store.seed_defaults(DEFAULT_WIFI_SSIDS)

    # Profile store + first-boot seeding from the YAML templates.
    profile_store = ProfileStore(session_factory)
    app.state.profile_store = profile_store
    if await profile_store.is_empty():
        seeds = app.state.profile_manager.list_all()
        await profile_store.seed_from(seeds)

    # Auto-seeding of procedural defaults is intentionally OFF — empty
    # slots fall back to wallpaper_studio.render_wallpaper at activation
    # time, which produces the proper cyber-theme PNG using the Slate's
    # own TTF fonts.

    # SSH keypair store (per-device). If the default device already has a
    # deployed keypair, switch its SSH channel to use it so we don't fall
    # back to password unnecessarily. Per-device routing of multi-device
    # SSH channels is a future iteration ; today only the default device
    # is wired into app.state.slate_ssh.
    ssh_keypair_store = SSHKeypairStore(session_factory)
    app.state.ssh_keypair_store = ssh_keypair_store
    try:
        ssh_status = await ssh_keypair_store.get_status(default_device.slug)
    except Exception as exc:  # noqa: BLE001 — boot must keep going
        logger.warning("ssh_keypair.boot_status_failed", error=str(exc))
        ssh_status = None
    if ssh_status and ssh_status.generated and ssh_status.deployed_to_slate:
        private_pem = await ssh_keypair_store.get_private_pem(default_device.slug)
        if private_pem:
            await app.state.slate_ssh.use_private_key(private_pem)
            logger.info(
                "slate_ssh.using_stored_keypair", device=default_device.slug,
            )

    # AdGuard manager — pilots /etc/init.d/adguardhome via SSH + talks to the
    # REST API on :3000 for stats and filters.
    # We reuse the controller's admin credentials so the user has a single
    # password to remember (the AdGuard web UI then accepts the same login).
    app.state.adguard_manager = AdGuardManager(
        ssh=app.state.slate_ssh,
        slate_host=app.state.slate_ssh.host,
        admin_username=settings.admin_username,
        admin_password=settings.admin_password,
    )

    # DNS protection: per-network security levels applied via AdGuard Clients API.
    from app.dns.manager import DnsProtectionManager, DnsProtectionStore
    from app.dns.store import DnsSecurityLevelStore

    app.state.dns_security_level_store = DnsSecurityLevelStore(session_factory)
    # Seed FACTORY_LEVELS into the DB if missing — idempotent. User edits to
    # existing rows are NOT overwritten.
    await app.state.dns_security_level_store.ensure_seeded()
    app.state.dns_protection_store = DnsProtectionStore(session_factory)
    app.state.dns_protection_manager = DnsProtectionManager(
        store=app.state.dns_protection_store,
        networks=network_store,
        adguard=app.state.adguard_manager,
        levels=app.state.dns_security_level_store,
    )

    # Security Device Status: SBOM + CVE match (OSV) + attack-path (CVE2CAPEC)
    # + exploit enrichment (KEV/EPSS/Exploit-DB/GitHub PoC/Metasploit).
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.scheduler.security_refresh import register_security_jobs
    from app.security.enrichers.cve2capec import Cve2CapecEnricher
    from app.security.exploit_enricher import ExploitEnricher
    from app.security.scanner import SecurityScanner
    from app.security.sources.osv import OsvSource
    from app.security.store import SecurityStore

    app.state.security_store = SecurityStore(session_factory)
    osv_source = OsvSource()
    cve2capec = Cve2CapecEnricher(session_factory)
    exploit_enricher = ExploitEnricher(session_factory)
    app.state.security_scanner = SecurityScanner(
        sources=[osv_source],
        enricher=cve2capec,
        exploit_enricher=exploit_enricher,
    )
    app.state.exploit_enricher = exploit_enricher
    app.state._security_osv_source = osv_source
    app.state._security_cve2capec = cve2capec

    # Single scheduler shared by future jobs. Daily exploit-sources refresh
    # at 06:00 UTC is the only one wired today.
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_security_jobs(scheduler, exploit_enricher)
    scheduler.start()
    app.state.scheduler = scheduler

    # Warm exploit sources in the background on startup so the UI shows
    # populated counts within ~10s of boot, without blocking the lifespan
    # on a 30s download.
    import asyncio as _asyncio

    async def _warm_sources() -> None:
        try:
            await exploit_enricher.ensure_sources_loaded()
        except Exception as exc:  # noqa: BLE001
            logger.warning("security.sources.warm_failed", error=str(exc))

    app.state._security_warmup_task = _asyncio.create_task(_warm_sources())

    # Tailscale exit-node HA watchdog — background loop. No-op if disabled
    # in the HA store; the user toggles it from /vpn/tailscale.
    from app.tailscale.client import TailscaleClient as _TSClient
    from app.tailscale.ha_store import TailscaleHAStore
    from app.tailscale.ha_watchdog import run_watchdog

    app.state.tailscale_ha_store = TailscaleHAStore(session_factory)
    app.state._tailscale_ha_task = _asyncio.create_task(
        run_watchdog(
            _TSClient(app.state.slate_ssh),
            app.state.tailscale_ha_store,
        )
    )

    try:
        yield
    finally:
        logger.info("slate_controller.stopping")
        app.state.scheduler.shutdown(wait=False)
        # Stop the watchdog before tearing down SSH — otherwise its in-flight
        # ssh.run() call could raise during cleanup.
        app.state._tailscale_ha_task.cancel()
        try:
            await app.state._tailscale_ha_task
        except (Exception, _asyncio.CancelledError):  # noqa: BLE001
            pass
        await app.state.slate_client.disconnect()
        await app.state.slate_ssh.close()
        await app.state.adguard_manager.aclose()
        await app.state._security_osv_source.aclose()
        await app.state._security_cve2capec.aclose()
        await app.state.exploit_enricher.aclose()
        await app.state.proton_client.logout()
        await app.state.proton_client.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Slate Controller API",
        description="API pour piloter un GL.iNet Slate 7 Pro.",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        # Restrict to the verbs the API actually uses. Wildcard was a holdover
        # from the FastAPI quickstart; an explicit list is auditable and
        # mirrors what the route table actually exposes.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # Headers stay wildcard because Axios + interceptors send Content-Type,
        # Authorization, plus assorted X-* on uploads — easier to keep open
        # and rely on the explicit origin allowlist for safety.
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok", "version": __version__}

    app.include_router(auth_routes.router, prefix="/api")
    app.include_router(profile_routes.router, prefix="/api")
    app.include_router(slate_routes.router, prefix="/api")
    app.include_router(proton_routes.router, prefix="/api")
    app.include_router(vpn_config_routes.router, prefix="/api")
    app.include_router(wifi_routes.router, prefix="/api")
    app.include_router(network_routes.router, prefix="/api")
    app.include_router(settings_routes.router, prefix="/api")
    app.include_router(adguard_routes.router, prefix="/api")
    app.include_router(device_routes.router, prefix="/api")
    app.include_router(security_routes.router, prefix="/api")
    app.include_router(tailscale_routes.router, prefix="/api")
    app.include_router(agent_routes.router, prefix="/api")
    app.include_router(dns_protection_routes.router, prefix="/api")

    return app


app = create_app()
