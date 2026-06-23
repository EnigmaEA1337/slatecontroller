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
from app.api.routes import controller_https as controller_https_routes
from app.api.routes import devices as device_routes
from app.api.routes import internal_ca as internal_ca_routes
from app.api.routes import dns_protection as dns_protection_routes
from app.api.routes import firewall as firewall_routes
from app.api.routes import networks as network_routes
from app.api.routes import profiles as profile_routes
from app.api.routes import proton as proton_routes
from app.api.routes import security as security_routes
from app.api.routes import settings as settings_routes
from app.api.routes import slate as slate_routes
from app.api.routes import tailscale as tailscale_routes
from app.api.routes import tor as tor_routes
from app.api.routes import vpn_configs as vpn_config_routes
from app.api.routes import fortinet as fortinet_routes
from app.api.routes import air_watch as air_watch_routes
from app.api.routes import ambient_scan as ambient_scan_routes
from app.api.routes import anti_theft as anti_theft_routes
from app.api.routes import ap_reviews as ap_reviews_routes
from app.api.routes import device_locations as device_locations_routes
from app.api.routes import scan_history as scan_history_routes
from app.api.routes import pcap as pcap_routes
from app.api.routes import slate_webhooks as slate_webhooks_routes
from app.api.routes import surveillance as surveillance_routes
from app.api.routes import wifi_orphans as wifi_orphans_routes
from app.api.routes import wifi as wifi_routes
from app.api.routes import wifi_radio as wifi_radio_routes
from app.api.routes import recon as recon_routes
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
from app.wifi.store import WifiSsidStore

# No default networks anymore. A fresh controller install starts with
# an EMPTY network catalog — the user creates networks as they need
# via the Networks page (Settings → Network → Add). The 5 cyberpunk
# seeds that used to live here were demo content that pre-populated
# every install, which was confusing on real deployments.

# No default Wi-Fi catalog anymore. Same philosophy as DEFAULT_NETWORKS :
# a fresh install starts with an EMPTY catalog — the user creates their
# SSIDs as they need via the Radio page (or imports from the live Slate
# via POST /api/wifi/discover-from-slate).

logger = structlog.get_logger(__name__)


# Placeholder JWT_SECRET values that ship with the repo or appear in
# shoddy onboarding examples. Booting the controller with any of these
# is a P0 security finding (nightly audit 2026-06-23) — the same secret
# is reused as the Fernet at-rest key, so an attacker forges admin
# tokens AND decrypts every stored device/VPN/WiFi password. The guard
# can be bypassed with ALLOW_PLACEHOLDER_JWT_SECRET=1 for dev/test only.
_PLACEHOLDER_JWT_SECRETS = frozenset({
    "dev-secret-change-me",
    "change-me",
    "change-me-to-a-long-random-string",
    "changeme",
    "secret",
    "",
})


def _refuse_placeholder_jwt_secret(settings) -> None:
    """Block startup when JWT_SECRET is a well-known placeholder.

    See _PLACEHOLDER_JWT_SECRETS for the blacklist. We also reject any
    secret shorter than 32 characters since HS256 with a low-entropy
    key is brute-forceable in minutes.
    """
    if settings.allow_placeholder_jwt_secret:
        logger.warning(
            "config.jwt_secret.placeholder_allowed",
            reason="ALLOW_PLACEHOLDER_JWT_SECRET=1 — dev/test only",
        )
        return
    if settings.jwt_secret in _PLACEHOLDER_JWT_SECRETS:
        raise RuntimeError(
            "JWT_SECRET is a known placeholder value. Set the JWT_SECRET "
            "env var to a long random string (>= 32 chars). For dev/test "
            "ONLY, set ALLOW_PLACEHOLDER_JWT_SECRET=1 to bypass. Note : "
            "this same secret is used to derive the Fernet at-rest key — "
            "changing it later DESTROYS every encrypted password in the "
            "database."
        )
    if len(settings.jwt_secret) < 32:
        raise RuntimeError(
            f"JWT_SECRET is too short ({len(settings.jwt_secret)} chars). "
            "Use at least 32 random characters (suggested : `openssl rand "
            "-base64 48`). Bypass with ALLOW_PLACEHOLDER_JWT_SECRET=1 in "
            "dev only."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    settings = get_settings()
    _refuse_placeholder_jwt_secret(settings)
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

    # Auto-seed sentinel : we only seed from .env ONCE in the lifetime
    # of this DB. After that the user is the source of truth — if they
    # delete the device, it stays deleted, even after a backend restart.
    # Sentinel key persists in app_state so the user's choice survives
    # container rebuilds.
    from sqlalchemy import select as _select
    from app.db.models import AppStateRow as _AppStateRow
    _SEED_FLAG_KEY = "device_env_seeded"
    async with session_factory() as _s:
        _seed_row = await _s.scalar(
            _select(_AppStateRow).where(_AppStateRow.key == _SEED_FLAG_KEY),
        )
        _already_seeded = _seed_row is not None and _seed_row.value == "1"

    default_device = await device_store.get_default()

    # One-time migration : si on a déjà un device par défaut mais pas
    # de sentinel (install d'avant ce patch), pose le sentinel maintenant.
    # Ça garantit que si l'utilisateur supprime ce device plus tard, on
    # ne le re-seed pas au prochain reboot du backend.
    if default_device is not None and not _already_seeded:
        async with session_factory() as _s:
            _s.add(_AppStateRow(key=_SEED_FLAG_KEY, value="1"))
            await _s.commit()
        _already_seeded = True
        logger.info("devices.seed_sentinel_backfilled")

    if default_device is None and not _already_seeded:
        # First boot ever → seed one from the .env values.
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
        # Pose le sentinel : plus jamais de re-seed automatique, même si
        # l'utilisateur supprime ce device plus tard.
        async with session_factory() as _s:
            _s.add(_AppStateRow(key=_SEED_FLAG_KEY, value="1"))
            await _s.commit()
        logger.info("devices.seeded_from_env", slug=default_device.slug, host=host)
    elif default_device is None and _already_seeded:
        logger.info("devices.no_default_present", reason="user_removed_no_auto_reseed")

    # SSH keypair store needs to exist before the registry builds devices
    # so the registry can auto-switch to key auth on cold builds.
    ssh_keypair_store = SSHKeypairStore(session_factory)
    app.state.ssh_keypair_store = ssh_keypair_store

    # Per-device connection registry — lazy cache of (slug → SlateClient,
    # SlateSSH, URL resolver, AdGuardManager). Replaces the previous
    # singleton-per-default-device pattern: every route now resolves its
    # device through the registry. Existing routes get the default device
    # via the unchanged DI helpers (`get_slate_client` etc.) ; new routes
    # take an optional `?device=<slug>` query param to target a specific
    # one. Switching the default device no longer requires a backend
    # restart — the new default is picked up on the next request.
    from app.devices.registry import DeviceConnectionsRegistry

    device_registry = DeviceConnectionsRegistry(
        device_store=device_store,
        ssh_keypair_store=ssh_keypair_store,
        settings=settings,
    )
    app.state.device_registry = device_registry

    # Pre-warm the default device so the first request after boot doesn't
    # pay the cold-build cost (resolver probe + SSH connect, ~200-500ms).
    # Also surfaces config errors at boot time instead of on first call.
    # Guard for the case where the user removed every device (and we now
    # honour that choice without auto-reseeding) : the controller boots
    # in "no device" mode and routes that need slate_* return clear 4xx
    # errors instead of crashing the startup.
    if await device_store.get_default() is not None:
        default_conn = await device_registry.for_default()
        app.state.slate_url_resolver = default_conn.url_resolver
        app.state.slate_client = default_conn.client
        app.state.slate_ssh = default_conn.ssh
        app.state.adguard_manager = default_conn.adguard
    else:
        # No device registered. Background tasks + routes that need a
        # default device will check these for None and degrade
        # gracefully.
        app.state.slate_url_resolver = None
        app.state.slate_client = None
        app.state.slate_ssh = None
        app.state.adguard_manager = None
        logger.info("controller.no_device_mode", reason="no default device")

    # Networks catalog. No seeding — the user creates networks
    # explicitly via the UI. Existing rows from previous installs
    # stay untouched ; the migration drops the is_builtin column so
    # they're all user-managed now.
    network_store = NetworkStore(session_factory)
    app.state.network_store = network_store

    # Wi-Fi catalog. No more default seeds — fresh installs start with
    # an empty catalog and the user creates SSIDs via the UI or imports
    # them from the live Slate (POST /api/wifi/discover-from-slate).
    wifi_store = WifiSsidStore(session_factory)
    app.state.wifi_store = wifi_store

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

    # NOTE: SSH keypair load + AdGuardManager init used to live here.
    # Both are now handled by `DeviceConnectionsRegistry._build` per
    # device, so multi-device support comes for free.

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
    # at 06:00 UTC + ambient-scan loops + ambient cleanup are the wired
    # ones today.
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_security_jobs(scheduler, exploit_enricher)
    scheduler.start()
    app.state.scheduler = scheduler

    # Ambient-scan manager : reads enabled configs and (re)schedules a
    # job per (device, band). Lives for the app lifetime.
    from app.scheduler.ambient_scan import AmbientScanManager
    ambient_mgr = AmbientScanManager(
        scheduler=scheduler,
        session_factory=session_factory,
        device_registry=device_registry,
    )
    app.state.ambient_scan_manager = ambient_mgr

    # LAN PCAP capture manager — orchestrates tcpdump-over-SSH sessions.
    from app.wifi.pcap_capture import PcapCaptureManager
    app.state.pcap_manager = PcapCaptureManager(session_factory)

    # Surveillance-session manager : re-schedules every still-active
    # session's per-band jobs + runs the deadline supervisor.
    from app.scheduler.surveillance import SurveillanceManager
    surveillance_mgr = SurveillanceManager(
        scheduler=scheduler,
        session_factory=session_factory,
        device_registry=device_registry,
    )
    app.state.surveillance_manager = surveillance_mgr

    # PIN bruteforce lockout : stateless service backed by SQLite.
    # Lives on app.state for the whole app lifetime ; no init needed.
    from app.security.anti_theft import AntiTheftService
    from app.security.pin_lockout import PinLockoutService
    pin_lockout_svc = PinLockoutService(session_factory)
    anti_theft_svc = AntiTheftService(
        session_factory=session_factory,
        device_registry=device_registry,
    )
    # Wire the two together so PIN failures escalate via anti-theft
    # autonomous mode when enabled.
    pin_lockout_svc.attach_anti_theft(anti_theft_svc)
    app.state.pin_lockout = pin_lockout_svc
    app.state.anti_theft = anti_theft_svc

    # Bridge gl_screen → anti-theft. Push is the primary path — the
    # Slate-side watcher pushes within ~2s of any state change AND
    # heartbeats every 30s, so the controller's snapshot stays fresh
    # on its own. The SSH poll is now a SAFETY NET only : it kicks in
    # at 5 min intervals to recover from a crashed/stopped on-device
    # watcher, an HMAC secret out-of-sync, or a controller restart
    # that lost the in-memory snapshot. At 300s it's invisible cost on
    # SSH (one tiny `cat` per device per 5 min).
    from app.scheduler.screen_lock_watcher import ScreenLockWatcher
    screen_lock_watcher = ScreenLockWatcher(
        scheduler=scheduler,
        device_registry=device_registry,
        anti_theft=anti_theft_svc,
        poll_interval_s=300,
    )
    screen_lock_watcher.register()
    app.state.screen_lock_watcher = screen_lock_watcher

    # Webhook infrastructure : HMAC auth + dispatcher + handlers.
    from app.webhooks.auth import WebhookAuthService
    from app.webhooks.dispatcher import WebhookDispatcher
    from app.webhooks.handlers import build_touchscreen_status_handler
    webhook_auth = WebhookAuthService(session_factory)
    webhook_dispatcher = WebhookDispatcher()
    webhook_dispatcher.register(
        "touchscreen_status",
        build_touchscreen_status_handler(screen_lock_watcher),
    )
    app.state.webhook_auth = webhook_auth
    app.state.webhook_dispatcher = webhook_dispatcher

    import asyncio as _asyncio

    # Tailscale exit-node HA watchdog : same gating — pointless to probe
    # a non-existent Slate. The store stays alive (the UI reads it).
    from app.tailscale.client import TailscaleClient as _TSClient
    from app.tailscale.ha_store import TailscaleHAStore
    from app.tailscale.ha_watchdog import run_watchdog

    app.state.tailscale_ha_store = TailscaleHAStore(session_factory)

    async def _warm_sources() -> None:
        try:
            await exploit_enricher.ensure_sources_loaded()
        except Exception as exc:  # noqa: BLE001
            logger.warning("security.sources.warm_failed", error=str(exc))

    def _start_post_adoption_services() -> None:
        """Kick off background tasks that only make sense once at least one
        device is adopted : CVE feed warmup + Tailscale HA watchdog.

        Idempotent : if a task is already running, we don't start a
        second one. Called both at boot (if a device is already adopted
        from a previous session) and from the adoption route after the
        first successful adoption.
        """
        cur_w = getattr(app.state, "_security_warmup_task", None)
        if cur_w is None or cur_w.done():
            app.state._security_warmup_task = _asyncio.create_task(_warm_sources())
            logger.info("post_adoption.security_warmup.started")
        cur_t = getattr(app.state, "_tailscale_ha_task", None)
        if cur_t is None or cur_t.done():
            # Resolve the active SSH bundle via the device registry instead
            # of relying on the (possibly None) singleton app.state.slate_ssh
            # — nightly audit 2026-06-23 low : without a default device
            # at boot, `app.state.slate_ssh` is None and TSClient(None)
            # crashed the post-adoption hook.
            async def _ha_watchdog_runner() -> None:
                try:
                    conn = await app.state.device_registry.for_default()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "post_adoption.ha_watchdog.no_default_device",
                        error=str(exc),
                    )
                    return
                await run_watchdog(
                    _TSClient(conn.ssh),
                    app.state.tailscale_ha_store,
                )

            app.state._tailscale_ha_task = _asyncio.create_task(
                _ha_watchdog_runner(), name="tailscale-ha-watchdog",
            )
            logger.info("post_adoption.ha_watchdog.started")
        # Ambient scan : restore whatever was enabled before shutdown.
        # Same idempotent pattern as above — register_all() replaces by id.
        cur_a = getattr(app.state, "_ambient_scan_boot_task", None)
        if cur_a is None or cur_a.done():
            app.state._ambient_scan_boot_task = _asyncio.create_task(
                ambient_mgr.register_all(),
            )
            logger.info("post_adoption.ambient_scan.started")
        # Surveillance : re-attach still-active sessions whose deadline
        # hasn't passed ; finalize the rest.
        cur_s = getattr(app.state, "_surveillance_boot_task", None)
        if cur_s is None or cur_s.done():
            app.state._surveillance_boot_task = _asyncio.create_task(
                surveillance_mgr.register_all(),
            )
            logger.info("post_adoption.surveillance.started")

    # Expose the starter on app.state so the adoption route can call it
    # after marking the first device as adopted.
    app.state.start_post_adoption_services = _start_post_adoption_services

    # Boot-time decision : do we already have an adopted device from
    # a previous run ? If yes, kick off the post-adoption services now.
    # Otherwise wait for the user to finish adoption — the route will
    # call `start_post_adoption_services()` then.
    adopted_rows = [
        d for d in await device_store.list_all() if d.status == "adopted"
    ]
    if adopted_rows:
        logger.info(
            "post_adoption.boot_kick", adopted_count=len(adopted_rows),
        )
        _start_post_adoption_services()
    else:
        # Make the placeholders explicit so the teardown code below
        # doesn't AttributeError when no adoption happened this run.
        app.state._security_warmup_task = None
        app.state._tailscale_ha_task = None
        logger.info(
            "post_adoption.deferred",
            reason="no adopted device — watchdog + CVE warmup deferred",
        )

    # OUI registry refresh — fire-and-forget so it doesn't delay startup.
    # The scanner only enriches when the registry is loaded ; otherwise
    # neighbour.vendor stays empty and the UI shows a "?" cell. Re-runs
    # every REFRESH_INTERVAL_S (a week) checked at each call.
    # Track the task on app.state so the lifespan teardown can cancel it
    # — without the reference the coroutine could outlive engine.dispose
    # and emit "Task was destroyed but it is pending!" warnings on
    # reload (nightly audit 2026-06-23 medium).
    try:
        from app.wifi.oui import refresh_async as _oui_refresh
        import asyncio as _aio
        app.state._oui_refresh_task = _aio.create_task(
            _oui_refresh(), name="oui-refresh-boot",
        )
    except Exception as exc:  # noqa: BLE001 — boot must keep going
        logger.warning("wifi.oui.boot_task_failed", error=str(exc))

    try:
        yield
    finally:
        logger.info("slate_controller.stopping")
        app.state.scheduler.shutdown(wait=False)
        # Cancel EVERY tracked background task before tearing down their
        # dependencies (SSH bundles, engine). The nightly audit
        # 2026-06-23 flagged the previous code as only cancelling the
        # HA watchdog — security_warmup / ambient_scan / surveillance
        # boot tasks (and a fire-and-forget OUI refresh) were left to
        # race the SSH close and the engine.dispose, producing
        # "Task was destroyed but it is pending!" warnings and
        # occasional asyncssh.ConnectionLost on reload. Gather the
        # cancellations so a slow one doesn't block the others.
        background_attrs = (
            "_tailscale_ha_task",
            "_security_warmup_task",
            "_ambient_scan_boot_task",
            "_surveillance_boot_task",
            "_oui_refresh_task",  # captured by _start_oui_refresh below
        )
        pending = []
        for name in background_attrs:
            task = getattr(app.state, name, None)
            if task is not None and not task.done():
                task.cancel()
                pending.append(task)
        if pending:
            try:
                await _asyncio.wait_for(
                    _asyncio.gather(*pending, return_exceptions=True),
                    timeout=10.0,
                )
            except _asyncio.TimeoutError:
                logger.warning(
                    "lifespan.shutdown.tasks_timeout",
                    pending=[t.get_name() for t in pending if not t.done()],
                )
        # Close every per-device bundle in the registry. This supersedes
        # the old "close the singleton client+ssh+adguard" trio — each
        # bundle's aclose() handles them all.
        await app.state.device_registry.aclose_all()
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
    # Webhook ingestion (Slate → Controller push). NO JWT — HMAC-only.
    app.include_router(slate_webhooks_routes.router, prefix="/api")
    app.include_router(profile_routes.router, prefix="/api")
    app.include_router(slate_routes.router, prefix="/api")
    app.include_router(proton_routes.router, prefix="/api")
    app.include_router(vpn_config_routes.router, prefix="/api")
    app.include_router(fortinet_routes.router, prefix="/api")
    # IMPORTANT : wifi_radio must register BEFORE wifi_routes — both share
    # the /wifi prefix, and wifi_routes has a catch-all `GET /wifi/{slug}`
    # for SSID lookup that would shadow `/wifi/radios` otherwise.
    # Same shadow concern as wifi_radio : scan_history's /wifi/scan-history
    # must register BEFORE wifi_routes' /wifi/{slug}.
    app.include_router(wifi_radio_routes.router, prefix="/api")
    app.include_router(scan_history_routes.router, prefix="/api")
    # ap_reviews is /wifi/reviews — also needs to land before the wifi
    # catch-all that owns /wifi/{slug}.
    app.include_router(ap_reviews_routes.router, prefix="/api")
    # bssid_router is the per-BSSID override layer on top of ap_reviews.
    app.include_router(ap_reviews_routes.bssid_router, prefix="/api")
    # ambient_scan owns /wifi/ambient — also before the catch-all.
    app.include_router(ambient_scan_routes.router, prefix="/api")
    # wifi orphans owns /wifi/orphans — same shadow concern as the rest.
    app.include_router(wifi_orphans_routes.router, prefix="/api")
    # surveillance owns /wifi/surveillance — same shadow concern.
    app.include_router(surveillance_routes.router, prefix="/api")
    # PCAP capture — /network/pcap.
    app.include_router(pcap_routes.router, prefix="/api")
    # WAN/LAN reconnaissance — /recon/scans, /recon/interfaces.
    app.include_router(recon_routes.router, prefix="/api")
    app.include_router(wifi_routes.router, prefix="/api")
    app.include_router(air_watch_routes.router, prefix="/api")
    app.include_router(device_locations_routes.router, prefix="/api")
    app.include_router(network_routes.router, prefix="/api")
    app.include_router(settings_routes.router, prefix="/api")
    app.include_router(adguard_routes.router, prefix="/api")
    app.include_router(device_routes.router, prefix="/api")
    app.include_router(security_routes.router, prefix="/api")
    app.include_router(anti_theft_routes.router, prefix="/api")
    app.include_router(tailscale_routes.router, prefix="/api")
    app.include_router(agent_routes.router, prefix="/api")
    app.include_router(dns_protection_routes.router, prefix="/api")
    app.include_router(firewall_routes.router, prefix="/api")
    app.include_router(tor_routes.router, prefix="/api")
    app.include_router(controller_https_routes.router, prefix="/api")
    app.include_router(internal_ca_routes.router, prefix="/api")

    return app


app = create_app()
