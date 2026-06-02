"""Routes for the slate-controller local agent.

The agent is a set of shell scripts deployed to the Slate at
`/etc/slate-controller/` + `/usr/local/bin/slate-ctrl`. Once installed, the
Slate can apply profiles locally — without the controller having to SSH
each command — which is what makes the physical button + boot-time
re-apply work even when the controller is offline.

Endpoints:
  GET  /api/agent/status        Where do we stand? Installed? Which version?
  POST /api/agent/deploy        Push slate-ctrl + handlers to the Slate.
  POST /api/agent/sync          Push profile JSONs to the Slate.
  POST /api/agent/apply/{name}  Call `slate-ctrl apply <name>` on the Slate.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.api.deps import (
    get_network_store,
    get_profile_store,
    get_slate_ssh,
    get_wifi_store,
)
from app.networks.store import NetworkStore
from app.auth import User, get_current_user
from app.config import get_settings
from app.profiles.store import ProfileStore
from app.slate.ssh import SlateSSH
from app.wifi.store import WifiSsidStore
from app.db.database import make_session_factory
from app.profiles.wallpapers import WallpaperStore
from app.slate_agent.deploy import deploy_agent, get_agent_version
from app.settings.button_cycle import ButtonCycleStore
from app.slate_agent.sync import (
    REBOOT_SENTINEL,
    apply_remote_profile,
    finalize_after_reboot,
    get_active_remote_profile,
    list_remote_profiles,
    refresh_button_cycle_active,
    sync_button_cycle,
    sync_loading_screens,
    sync_profile_wallpapers,
    sync_profiles,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/status")
async def agent_status(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Best-effort introspection of the agent's deployment state."""
    version = await get_agent_version(ssh)
    if version is None:
        return {
            "installed": False,
            "version": None,
            "remote_profiles": [],
            "active": None,
        }
    return {
        "installed": True,
        "version": version,
        "remote_profiles": await list_remote_profiles(ssh),
        "active": await get_active_remote_profile(ssh),
    }


@router.post("/deploy")
async def agent_deploy(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Push slate-ctrl + handlers + AdGuard creds + Wi-Fi PSKs to the Slate. Idempotent.

    The AdGuard secret is only pushed if the controller has non-placeholder
    credentials (i.e. someone has overridden ADMIN_PASSWORD in .env). If
    the defaults are still in place, the secret push is skipped — re-run
    /api/agent/deploy after changing the env vars.

    Wi-Fi PSKs are read from the WifiSsidStore for every SSID that has
    one set. Pushed as wifi.env (chmod 600). The wifi.sh handler sources
    this file when it has to CREATE a wifi-iface that's missing on the
    Slate (e.g. first-time deploy of a profile-defined SSID).
    """
    settings = get_settings()

    # Only ship credentials we trust. The `_write_adguard_secret` helper
    # also rejects placeholders, but skipping the call entirely keeps the
    # deploy report cleaner ("not pushed" vs an error).
    adguard_creds: tuple[str, str] | None = None
    if (
        settings.admin_password
        and settings.admin_password.strip().lower() not in {"change-me", "changeme", "password"}
    ):
        adguard_creds = (settings.admin_username, settings.admin_password)

    # Collect every SSID with a stored PSK. Failures decoding any
    # individual PSK don't fail the whole deploy ; the slug just
    # doesn't end up in wifi.env (handler will refuse to CREATE that
    # SSID with a clear log line).
    wifi_psks: dict[str, str] = {}
    try:
        for entry in await wifi.list_all():
            if not entry.has_password:
                continue
            try:
                wifi_psks[entry.slug] = await wifi.get_password(entry.slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "agent.deploy.wifi_psk_skip",
                    slug=entry.slug, error=str(exc),
                )
    except Exception as exc:  # noqa: BLE001 — never fail the deploy
        logger.warning("agent.deploy.wifi_list_failed", error=str(exc))

    report = await deploy_agent(
        ssh,
        adguard_credentials=adguard_creds,
        wifi_passwords=wifi_psks or None,
    )
    logger.info(
        "agent.deploy",
        username=user.username, ok=report.ok, errors=len(report.errors),
        adguard_secret_pushed=adguard_creds is not None,
        wifi_psks_pushed=len(wifi_psks),
    )
    return report.to_dict()


@router.post("/sync")
async def agent_sync(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    networks: Annotated[NetworkStore, Depends(get_network_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Push profile JSONs + pre-rendered loading screens + wallpapers to
    the Slate.

    Three artifacts per profile :
      1. Enriched JSON → /etc/slate-controller/profiles/<name>.json
         (Pydantic dump + `wifi.ssids[*].name` resolved from the SSID
         catalog + `adguard.lists[]` resolved from the feeds catalog +
         `wallpaper: {home, lock}` flags indicating which kinds exist.)
      2. The "loading profile X" RGB565 raw (153 600 B) →
         /etc/slate-controller/screens/loading_<name>.raw
      3. Per-profile×kind wallpaper PNGs (320×240, fit_mode applied
         server-side using Pillow) → /etc/slate-controller/wallpapers/
         <profile>_<kind>.png — the wallpaper.sh handler copies them into
         /etc/gl_screen/ at apply time.

    Everything is versioned together — sync = push everything.
    """
    items = await store.list_all()
    profiles = [stored.profile for stored in items]
    wifi_catalog = await wifi.list_all()
    network_catalog = await networks.list_all()
    # Wallpaper + tailnet-admin stores are built per-call rather than
    # depended-on globally because session factory lifetime is tied to
    # request scope here.
    sf = make_session_factory(request.app.state.db_engine)
    wallpaper_store = WallpaperStore(sf)
    from app.settings.tailnet_admin import TailnetAdminStore
    tailnet_admin_store = TailnetAdminStore(sf)
    json_report = await sync_profiles(
        ssh, profiles,
        wifi_catalog=wifi_catalog,
        network_catalog=network_catalog,
        tailnet_admin_store=tailnet_admin_store,
        wallpaper_store=wallpaper_store,
    )
    screens_report = await sync_loading_screens(ssh, profiles)
    wallpapers_report = await sync_profile_wallpapers(
        ssh, profiles, wallpaper_store,
    )
    # Reset-button profile cycle. Push the configured cycle (or an empty
    # one) so the agent's cycle-profile.sh always has a fresh
    # `cycle.json` to read at button-press time.
    cycle_store = ButtonCycleStore(
        make_session_factory(request.app.state.db_engine),
    )
    cycle_steps = await cycle_store.get()
    try:
        active_name = await store.get_active_name()
    except Exception:  # noqa: BLE001 — best effort
        active_name = None
    cycle_report = await sync_button_cycle(
        ssh, cycle_steps, active_name=active_name,
    )
    logger.info(
        "agent.sync", username=user.username,
        count=len(profiles),
        json_ok=json_report.ok, screens_ok=screens_report.ok,
        wallpapers_ok=wallpapers_report.ok,
        cycle_ok=cycle_report.ok, cycle_steps=len(cycle_steps),
    )
    return {
        "ok": (
            json_report.ok
            and screens_report.ok
            and wallpapers_report.ok
            and cycle_report.ok
        ),
        "profiles": json_report.to_dict(),
        "screens": screens_report.to_dict(),
        "wallpapers": wallpapers_report.to_dict(),
        "cycle": cycle_report.to_dict(),
    }


@router.post("/apply/{name}")
async def agent_apply(
    name: str,
    request: Request,
    background_tasks: BackgroundTasks,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Invoke `slate-ctrl apply <name>` on the Slate.

    Different from `/api/profiles/{name}/activate`: that endpoint runs the
    Python appliers from the controller and SSHs each subsystem command.
    This endpoint hands the job to the local agent — the controller stays
    out of the apply loop entirely. Use this once the agent is deployed +
    handlers are mature; fall back to /activate if you need controller-
    side appliers that aren't yet ported to shell.
    """
    # Validate the profile exists controller-side too, so callers get a
    # clean 404 rather than a confusing shell error.
    try:
        await store.get(name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc

    ok, output = await apply_remote_profile(ssh, name)
    reboot_pending = REBOOT_SENTINEL in output
    logger.info(
        "agent.apply", username=user.username, name=name, ok=ok,
        reboot_pending=reboot_pending,
    )
    # Regenerate the on-Slate menu frames so the ACTIVE badge lands on the
    # right row. Best-effort — failures never fail the apply. When the agent
    # scheduled a reboot (radio changes), the box is going down: defer the
    # refresh to a background task that waits for it to come back, rather
    # than racing the reboot with an inline SSH call.
    if ok and reboot_pending:
        background_tasks.add_task(
            finalize_after_reboot, ssh, name, request.app.state.db_engine,
        )
    elif ok:
        try:
            cycle_store = ButtonCycleStore(
                make_session_factory(request.app.state.db_engine),
            )
            cycle_steps = await cycle_store.get()
            await refresh_button_cycle_active(ssh, cycle_steps, name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent.apply.cycle_refresh_failed",
                name=name, error=str(exc),
            )
    return {
        "ok": ok, "name": name, "output": output,
        "reboot_pending": reboot_pending,
    }
