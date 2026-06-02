"""Tor subsystem REST routes.

- ``GET/PUT /api/tor/settings``  global daemon switches.
- ``GET/POST /api/tor/bridges``   list/create bridge lines.
- ``PUT/DELETE /api/tor/bridges/{id}``  update/remove a bridge.
- ``GET /api/tor/status``         live snapshot from the Slate (SSH).
- ``POST /api/tor/install``       opkg install on demand (returns the log).

Per-network routing toggles (tor_route_mode / tor_dns_over_tor /
tor_kill_switch) live on the existing ``/api/networks/{slug}`` PUT — we
don't duplicate them here.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_network_store, get_profile_store, get_slate_ssh
from app.auth import User, get_current_user
from app.db.database import make_session_factory
from app.networks.store import NetworkStore
from app.profiles.store import ProfileStore
from app.slate.ssh import SlateSSH
from app.tor.audit import TorAuditReport, run_audit
from app.tor.client import (
    TorInstallError,
    fetch_status,
    install_packages,
)
from app.tor.models import (
    TorBridge,
    TorBridgeWrite,
    TorSettings,
    TorSettingsWrite,
    TorStatus,
)
from app.tor.store import TorBridgeNotFoundError, TorBridgeStore, TorSettingsStore

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tor", tags=["tor"])


def _settings_store(request: Request) -> TorSettingsStore:
    return TorSettingsStore(make_session_factory(request.app.state.db_engine))


def _bridge_store(request: Request) -> TorBridgeStore:
    return TorBridgeStore(make_session_factory(request.app.state.db_engine))


# ── Global settings ───────────────────────────────────────────────────


@router.get("/settings", response_model=TorSettings)
async def get_tor_settings(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> TorSettings:
    return await _settings_store(request).get()


@router.put("/settings", response_model=TorSettings)
async def update_tor_settings(
    body: TorSettingsWrite,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> TorSettings:
    saved = await _settings_store(request).save(body)
    logger.info(
        "tor.settings.updated",
        username=user.username,
        daemon_enabled=saved.daemon_enabled,
        use_bridges=saved.use_bridges,
    )
    return saved


# ── Bridges ────────────────────────────────────────────────────────────


@router.get("/bridges", response_model=list[TorBridge])
async def list_tor_bridges(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> list[TorBridge]:
    return await _bridge_store(request).list_all()


@router.post(
    "/bridges",
    response_model=TorBridge,
    status_code=status.HTTP_201_CREATED,
)
async def create_tor_bridge(
    body: TorBridgeWrite,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> TorBridge:
    bridge = await _bridge_store(request).create(body)
    logger.info(
        "tor.bridge.created",
        username=user.username, id=bridge.id, kind=bridge.kind,
    )
    return bridge


@router.put("/bridges/{bridge_id}", response_model=TorBridge)
async def update_tor_bridge(
    bridge_id: int,
    body: TorBridgeWrite,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> TorBridge:
    try:
        bridge = await _bridge_store(request).update(bridge_id, body)
    except TorBridgeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    logger.info(
        "tor.bridge.updated", username=user.username, id=bridge.id,
    )
    return bridge


@router.delete("/bridges/{bridge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tor_bridge(
    bridge_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await _bridge_store(request).delete(bridge_id)
    except TorBridgeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    logger.info(
        "tor.bridge.deleted", username=user.username, id=bridge_id,
    )


# ── Live status + install ──────────────────────────────────────────────


@router.get("/status", response_model=TorStatus)
async def get_tor_status(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> TorStatus:
    """Snapshot of the on-device Tor daemon. Cheap (~200 ms) when Tor is
    installed, near-instant otherwise. Designed to be polled by the UI
    every 5-10 s on the Tor section of the Networks page.
    """
    return await fetch_status(ssh)


@router.post("/apply")
async def apply_tor_now(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    profile_store: Annotated[ProfileStore, Depends(get_profile_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Per-area Save+Apply for Tor — re-sync the active profile JSON, then
    run ONLY the tor handler on the device. Designed for the "Save =
    Apply" UX : a settings PUT chains into this so the operator never
    has to think about pushing.

    Typical timing : 1-3 s of SSH (sync + slate-ctrl apply-only tor).
    Cheap enough to run on every Tor settings save.
    """
    from app.networks.store import NetworkStore as _NS
    from app.slate_agent.sync import (
        apply_single_handler,
        sync_profiles,
    )
    from app.tor.store import TorBridgeStore as _TBS, TorSettingsStore as _TSS
    # Sync_profiles requires the WiFi catalog so it can enrich the
    # `wifi.ssids[*]` payload with name/bands/security/etc. — without
    # it, every SSID gets marked `missing: True` and the handler skips
    # them, leaving the Slate's wireless state silently stale across
    # tor-only re-applies. Cf. bug B fix 2026-06-02.
    from app.wifi.store import WifiSsidStore as _WS

    active = await profile_store.get_active_name()
    if not active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aucun profil actif — activez un profil avant d'appliquer Tor.",
        )
    try:
        stored = await profile_store.get(active)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profil actif {active!r} introuvable",
        ) from exc

    sf = make_session_factory(request.app.state.db_engine)
    rep = await sync_profiles(
        ssh, [stored.profile],
        wifi_catalog=await _WS(sf).list_all(),
        network_catalog=await _NS(sf).list_all(),
        tor_settings_store=_TSS(sf),
        tor_bridge_store=_TBS(sf),
    )
    if not rep.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sync échoué : {rep.errors}",
        )

    ok, output = await apply_single_handler(ssh, "tor")
    logger.info(
        "tor.apply.area",
        username=user.username, active=active, ok=ok,
    )
    return {"ok": ok, "output": output, "applied": "tor", "active": active}


@router.get("/audit")
async def get_tor_audit(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    network_store: Annotated[NetworkStore, Depends(get_network_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Security audit of the Tor gateway posture.

    Read-only — runs a handful of probes (netstat, ip6tables, conntrack)
    over SSH and combines them with the controller's network catalog to
    produce a list of findings. The UI shows them with severity badges
    and remediation hints, like SecurityHardening.
    """
    report: TorAuditReport = await run_audit(ssh, network_store)
    return {
        "score": report.score,
        "tor_installed": report.tor_installed,
        "tor_running": report.tor_running,
        "transparent_networks": report.transparent_networks,
        "generated_at": report.generated_at.isoformat(),
        "findings": [
            {
                "id": f.id,
                "label": f.label,
                "status": f.status,
                "severity": f.severity,
                "evidence": f.evidence,
                "remediation": f.remediation,
            }
            for f in report.findings
        ],
    }


@router.get("/logs")
async def get_tor_logs(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = 200,
) -> dict[str, list[str]]:
    """Tail the on-device Tor notices log.

    Reads up to ``limit`` lines from ``/var/log/tor/notices.log`` (the
    path the handler emits when it writes torrc). Falls back to filtering
    ``logread`` so the UI gets *something* even when notices.log doesn't
    exist yet (fresh install / never-run daemon).
    """
    limit = max(10, min(limit, 2000))
    try:
        r = await ssh.run(
            f"tail -n {limit} /var/log/tor/notices.log 2>/dev/null || "
            f"logread 2>/dev/null | grep -iE 'tor\\b' | tail -n {limit} || true",
            timeout=12,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tor.logs_fetch_failed", error=str(exc))
        return {"lines": []}
    return {"lines": [ln for ln in r.stdout.splitlines() if ln]}


@router.post("/install")
async def install_tor(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    """Run ``opkg install tor tor-geoipdb obfs4proxy`` on the Slate. Slow
    (30-90 s) — the UI shows a spinner. Returns the install log so the
    user can see what happened.
    """
    try:
        log_output = await install_packages(ssh)
    except TorInstallError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    logger.info("tor.installed", username=user.username)
    return {"ok": "true", "output": log_output}
