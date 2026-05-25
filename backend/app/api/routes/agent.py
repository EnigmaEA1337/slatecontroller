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
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_profile_store, get_slate_ssh, get_wifi_store
from app.auth import User, get_current_user
from app.config import get_settings
from app.profiles.store import ProfileStore
from app.slate.ssh import SlateSSH
from app.wifi.store import WifiSsidStore
from app.db.database import make_session_factory
from app.profiles.wallpapers import WallpaperStore
from app.slate_agent.deploy import deploy_agent, get_agent_version
from app.slate_agent.sync import (
    apply_remote_profile,
    get_active_remote_profile,
    list_remote_profiles,
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
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Push slate-ctrl + handlers + AdGuard creds to the Slate. Idempotent.

    The AdGuard secret is only pushed if the controller has non-placeholder
    credentials (i.e. someone has overridden ADMIN_PASSWORD in .env). If
    the defaults are still in place, the secret push is skipped — re-run
    /api/agent/deploy after changing the env vars.
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

    report = await deploy_agent(ssh, adguard_credentials=adguard_creds)
    logger.info(
        "agent.deploy",
        username=user.username, ok=report.ok, errors=len(report.errors),
        adguard_secret_pushed=adguard_creds is not None,
    )
    return report.to_dict()


@router.post("/sync")
async def agent_sync(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
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
    # Wallpaper store is built per-call rather than depended-on globally
    # because session factory lifetime is tied to request scope here.
    wallpaper_store = WallpaperStore(
        make_session_factory(request.app.state.db_engine),
    )
    json_report = await sync_profiles(
        ssh, profiles,
        wifi_catalog=wifi_catalog,
        wallpaper_store=wallpaper_store,
    )
    screens_report = await sync_loading_screens(ssh, profiles)
    wallpapers_report = await sync_profile_wallpapers(
        ssh, profiles, wallpaper_store,
    )
    logger.info(
        "agent.sync", username=user.username,
        count=len(profiles),
        json_ok=json_report.ok, screens_ok=screens_report.ok,
        wallpapers_ok=wallpapers_report.ok,
    )
    return {
        "ok": (
            json_report.ok and screens_report.ok and wallpapers_report.ok
        ),
        "profiles": json_report.to_dict(),
        "screens": screens_report.to_dict(),
        "wallpapers": wallpapers_report.to_dict(),
    }


@router.post("/apply/{name}")
async def agent_apply(
    name: str,
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
    logger.info(
        "agent.apply", username=user.username, name=name, ok=ok,
    )
    return {"ok": ok, "name": name, "output": output}
