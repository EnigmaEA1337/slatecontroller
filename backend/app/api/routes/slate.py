"""Routes that expose the live Slate state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.adguard.manager import AdGuardManager
from app.api.deps import (
    get_adguard_manager,
    get_exploit_enricher,
    get_security_store,
    get_slate_client,
    get_slate_ssh,
    get_slate_url_resolver,
)
from app.security.exploit_enricher import ExploitEnricher
from app.security.store import SecurityStore
from app.auth import User, get_current_user
from app.exceptions import SlateRpcError, SlateUnreachableError
from app.slate.client import SlateClient
from app.slate.hardening import HardeningCheck, HardeningReport, compute_hardening
from app.slate.screen_lock import (
    ScreenLockError,
    ScreenLockStatus,
    get_status as screen_lock_status,
    set_auto_lock as screen_lock_set_auto_lock,
    set_enabled as screen_lock_set_enabled,
    set_pin as screen_lock_set_pin,
)
from app.slate.ssh import SlateSSH
from app.slate.url_resolver import SlateUrlResolver

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/slate", tags=["slate"])


class SlateStatus(BaseModel):
    """Live Slate state.

    Fields are populated from `system.get_info` and `system.get_status`. Field
    paths reflect what GL.iNet firmware 4.8.x (Slate 7 Pro / GL-BE10000) returns;
    older/newer firmwares may omit some — all fields are optional and
    independently `None` on missing data.

    Security: WiFi SSID passwords visible in `system.get_status.wifi[]` are
    intentionally NOT exposed by this endpoint.
    """

    connected: bool
    timestamp: datetime

    # From system.get_info
    model: str | None = None
    firmware_version: str | None = None
    firmware_type: str | None = None
    hostname: str | None = None
    mac: str | None = None
    country_code: str | None = None
    cpu_count: int | None = None

    # From system.get_status.system
    uptime_seconds: float | None = None
    memory_total_bytes: int | None = None
    memory_free_bytes: int | None = None
    memory_usage_percent: float | None = None
    cpu_temperature_celsius: int | None = None
    load_average_1m: float | None = None
    load_average_5m: float | None = None
    load_average_15m: float | None = None
    lan_ip: str | None = None

    # Derived aggregates
    connected_clients: int | None = Field(
        default=None, description="Sum of wired + wireless clients."
    )
    wan_online: bool | None = Field(
        default=None, description="True iff at least one WAN-like interface reports online."
    )
    services: dict[str, bool] | None = Field(
        default=None,
        description="Service name → enabled (status==1). e.g. adguard, tor, tailscale.",
    )


def _unwrap(payload: Any) -> dict[str, Any]:
    """Extract the `result` dict from a pyglinet response envelope.

    pyglinet returns the full JSON-RPC envelope (`{id, jsonrpc, result: {...}}`).
    We only care about the inner `result`.
    """
    if payload is None:
        return {}
    # Prefer attribute access (ResultContainer-style)
    if hasattr(payload, "result") and not isinstance(payload, dict):
        inner = payload.result
        if isinstance(inner, dict):
            return inner
        return _unwrap(inner)
    # Dict-style envelope
    if isinstance(payload, dict):
        if "result" in payload and isinstance(payload["result"], dict):
            return payload["result"]
        return payload
    try:
        return dict(payload)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


def _build_status(info: dict[str, Any], live: dict[str, Any]) -> SlateStatus:
    """Map raw RPC payloads to the typed `SlateStatus`."""
    board = info.get("board_info") or {}
    sys = live.get("system") or {}
    cpu = sys.get("cpu") or {}
    load = sys.get("load_average") or []
    networks = live.get("network") or []
    clients = live.get("client") or []
    services = live.get("service") or []

    mem_total = sys.get("memory_total")
    mem_free = sys.get("memory_free")
    mem_pct: float | None = None
    if isinstance(mem_total, int) and isinstance(mem_free, int) and mem_total > 0:
        mem_pct = round((mem_total - mem_free) / mem_total * 100, 1)

    client_total: int | None = None
    if clients and isinstance(clients[0], dict):
        cable = clients[0].get("cable_total", 0) or 0
        wireless = clients[0].get("wireless_total", 0) or 0
        client_total = int(cable) + int(wireless)

    wan_online: bool | None = None
    wan_ifaces = {"wan", "wan6", "wwan", "wwan6", "tethering", "tethering6", "secondwan"}
    if networks:
        wan_online = any(
            iface.get("online")
            for iface in networks
            if isinstance(iface, dict) and iface.get("interface") in wan_ifaces
        )

    service_map: dict[str, bool] | None = None
    if services:
        service_map = {
            svc["name"]: bool(svc.get("status"))
            for svc in services
            if isinstance(svc, dict) and "name" in svc
        }

    return SlateStatus(
        connected=True,
        timestamp=datetime.now(UTC),
        model=info.get("model") or board.get("model"),
        firmware_version=info.get("firmware_version"),
        firmware_type=info.get("firmware_type"),
        hostname=board.get("hostname"),
        mac=info.get("mac"),
        country_code=info.get("country_code"),
        cpu_count=info.get("cpu_num"),
        uptime_seconds=sys.get("uptime"),
        memory_total_bytes=mem_total,
        memory_free_bytes=mem_free,
        memory_usage_percent=mem_pct,
        cpu_temperature_celsius=cpu.get("temperature"),
        load_average_1m=load[0] if len(load) > 0 else None,
        load_average_5m=load[1] if len(load) > 1 else None,
        load_average_15m=load[2] if len(load) > 2 else None,
        lan_ip=sys.get("lan_ip"),
        connected_clients=client_total,
        wan_online=wan_online,
        services=service_map,
    )


class HardeningCheckModel(BaseModel):
    name: str
    points: int
    max_points: int
    status: str
    note: str = ""

    @classmethod
    def of(cls, c: HardeningCheck) -> HardeningCheckModel:
        return cls(
            name=c.name,
            points=c.points,
            max_points=c.max_points,
            status=c.status,
            note=c.note,
        )


class HardeningResponse(BaseModel):
    score: int
    max_score: int
    percent: int
    reachable: bool
    checks: list[HardeningCheckModel]

    @classmethod
    def of(cls, r: HardeningReport) -> HardeningResponse:
        return cls(
            score=r.score,
            max_score=r.max_score,
            percent=r.percent,
            reachable=r.reachable,
            checks=[HardeningCheckModel.of(c) for c in r.checks],
        )


@router.get("/hardening", response_model=HardeningResponse)
async def get_hardening(
    slate: Annotated[SlateClient, Depends(get_slate_client)],
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    security_store: Annotated[SecurityStore, Depends(get_security_store)],
    exploit_enricher: Annotated[ExploitEnricher, Depends(get_exploit_enricher)],
    adguard: Annotated[AdGuardManager, Depends(get_adguard_manager)],
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> HardeningResponse:
    """Compute the device-level hardening gauge.

    Independent of which profile is active — measures the Slate itself
    (firmware, services, exposed protocols, SSH config) and now also factors
    in unacked critical/high CVE counts from the latest SBOM scan. Checks
    that fall back to `needs_probe` indicate something the backend can't
    read (or, for CVE, that no scan has been run yet).
    """
    # Resolve default device for the CVE check. Tolerate failures — the
    # hardening report still works without CVE integration.
    device_id: int | None = None
    try:
        from sqlalchemy import select
        from app.db.database import make_session_factory
        from app.db.models import DeviceRow

        sf = make_session_factory(request.app.state.db_engine)
        async with sf() as s:
            row = await s.scalar(select(DeviceRow).where(DeviceRow.is_default.is_(True)))
            if row is None:
                row = await s.scalar(select(DeviceRow).order_by(DeviceRow.id))
            if row is not None:
                device_id = row.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("hardening.device_lookup_failed", error=str(exc))

    report = await compute_hardening(
        slate,
        ssh=ssh,
        security_store=security_store,
        exploit_enricher=exploit_enricher,
        device_id=device_id,
        adguard_manager=adguard,
    )
    return HardeningResponse.of(report)


@router.get("/status", response_model=SlateStatus)
async def get_status(
    slate: Annotated[SlateClient, Depends(get_slate_client)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> SlateStatus:
    """Return the current Slate state.

    Returns 503 if the Slate is unreachable. Partial payloads are returned
    (connected=True, some fields None) when an individual RPC call fails.
    """
    info: dict[str, Any] = {}
    live: dict[str, Any] = {}

    try:
        try:
            info = _unwrap(await slate.call("system", "get_info"))
        except SlateRpcError as exc:
            logger.warning("slate.status.info_failed", error=str(exc))
        try:
            live = _unwrap(await slate.call("system", "get_status"))
        except SlateRpcError as exc:
            logger.warning("slate.status.status_failed", error=str(exc))
    except SlateUnreachableError as exc:
        logger.warning("slate.status.unreachable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slate unreachable",
        ) from exc

    return _build_status(info, live)


class ScreenMessageRequest(BaseModel):
    """Generic payload for `POST /api/slate/screen/message`.

    Sends an arbitrary terminal-style overlay onto the Slate's front screen
    by stopping gl_screen, writing raw RGB565 to /dev/fb0, holding N
    seconds, then restarting the daemon. See app.profiles.slate_message.
    """

    title: str = "hello from slate-controller"
    subtitle: str | None = "from slate-controller"
    target: str | None = None
    kind: str = "status"  # status | action | error | ok — colors the terminal frame
    duration_seconds: float = 4.0


@router.get("/screen/message/preview")
async def get_screen_message_preview(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    title: str = "MISE A JOUR",
    subtitle: str = "depuis Slate Controller",
    target: str | None = None,
    kind: str = "status",
):
    """Render the status overlay PNG (320×240) WITHOUT pushing to the Slate.

    Used by Settings → Communication to show a live preview of what the
    screen takeover will look like, without disrupting the panel.
    """
    from app.profiles.status_screen import render_status_image
    from fastapi.responses import Response
    if kind not in ("status", "action", "error", "ok"):
        kind = "status"
    png = await render_status_image(
        ssh,
        title=title or "MISE A JOUR",
        subtitle=subtitle or "depuis Slate Controller",
        target=target or None,
        kind=kind,  # type: ignore[arg-type]
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/screen/message")
async def post_screen_message(
    body: ScreenMessageRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Display an arbitrary message on the Slate's front screen.

    Useful for ad-hoc notifications, test from the UI, or as a side-channel
    indicator during long-running operations.
    """
    from app.profiles.slate_message import display_message

    kind_val = body.kind if body.kind in ("status", "action", "error", "ok") else "status"
    rep = await display_message(
        ssh,
        title=body.title,
        subtitle=body.subtitle or "from slate-controller",
        target=body.target,
        kind=kind_val,  # type: ignore[arg-type]
        duration_seconds=max(1.0, min(30.0, body.duration_seconds)),
        restart_after=True,
    )
    logger.info(
        "slate.screen.message",
        username=user.username,
        title=body.title,
        duration=body.duration_seconds,
        ok=rep.ok,
    )
    return rep.to_dict()


@router.get("/screen/snapshot")
async def get_screen_snapshot(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
):
    """Capture the Slate's front screen (live framebuffer) → PNG 320×240.

    Use this from the UI to identify "safe zones" where GL.iNet's widgets
    don't overlay the wallpaper — so you can design background images that
    place text outside those zones.
    """
    from app.slate.screen_capture import capture_screen_png
    from fastapi.responses import Response
    png = await capture_screen_png(ssh)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/connectivity")
async def get_connectivity(
    resolver: Annotated[SlateUrlResolver, Depends(get_slate_url_resolver)],
    _user: Annotated[User, Depends(get_current_user)],
    force_refresh: bool = False,
) -> dict:
    """Live probe of every admin URL configured on the default device.

    Returns each candidate's reachability + latency + which one is currently
    active. Used by the UI to show a "via LAN / via Tailscale / …" badge
    and to let the admin trigger a manual re-probe with `?force_refresh=1`.
    """
    if force_refresh:
        results = await resolver.force_refresh()
    else:
        results = resolver.last_results
        if not results:
            # First call after boot — nothing cached yet.
            results = await resolver.force_refresh()
    return {
        "active_url": resolver.active_url,
        "candidates": [
            {
                "url": r.url,
                "host": r.host,
                "reachable": r.reachable,
                "latency_ms": r.latency_ms,
            }
            for r in results
        ],
    }


# ---------------------------- screen lock ---------------------------- #


class _ScreenLockStatusOut(BaseModel):
    """Public-safe screen lock state — the PIN itself is never exposed.
    `pin_strength` is computed server-side so the UI can show a strength
    badge without ever receiving the actual digits."""

    enabled: bool
    has_pin: bool
    pin_length: int
    pin_strength: str  # "none" | "weak" | "medium" | "strong"
    auto_lock_seconds: int


def _screen_lock_to_dict(s: ScreenLockStatus) -> _ScreenLockStatusOut:
    return _ScreenLockStatusOut(
        enabled=s.enabled,
        has_pin=s.has_pin,
        pin_length=s.pin_length,
        pin_strength=s.pin_strength,
        auto_lock_seconds=s.auto_lock_seconds,
    )


class _SetPinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pin: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class _SetEnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class _SetAutoLockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: int = Field(ge=15, le=3600)


@router.get("/screen-lock", response_model=_ScreenLockStatusOut)
async def get_screen_lock(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> _ScreenLockStatusOut:
    """Read the touchscreen lock state (PIN never returned, only strength)."""
    try:
        st = await screen_lock_status(ssh)
    except ScreenLockError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    return _screen_lock_to_dict(st)


@router.put("/screen-lock/pin", response_model=_ScreenLockStatusOut)
async def set_screen_lock_pin(
    body: _SetPinBody,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> _ScreenLockStatusOut:
    """Set a new touchscreen PIN (4-8 digits). Also flips ENABLE_PASSCODE=1
    so the new PIN takes effect immediately."""
    try:
        st = await screen_lock_set_pin(ssh, body.pin)
    except ScreenLockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # NB: don't log the PIN itself — only structural info.
    logger.info(
        "slate.screen_lock.pin_set",
        username=user.username, pin_length=len(body.pin),
        pin_strength=st.pin_strength,
    )
    return _screen_lock_to_dict(st)


@router.put("/screen-lock/enabled", response_model=_ScreenLockStatusOut)
async def set_screen_lock_enabled(
    body: _SetEnabledBody,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> _ScreenLockStatusOut:
    """Toggle the lock screen on/off. PIN is preserved."""
    try:
        st = await screen_lock_set_enabled(ssh, body.enabled)
    except ScreenLockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "slate.screen_lock.enabled_set",
        username=user.username, enabled=body.enabled,
    )
    return _screen_lock_to_dict(st)


@router.put("/screen-lock/auto-lock", response_model=_ScreenLockStatusOut)
async def set_screen_lock_auto_lock(
    body: _SetAutoLockBody,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> _ScreenLockStatusOut:
    """Set the inactivity timer before auto-lock (15s to 1h)."""
    try:
        st = await screen_lock_set_auto_lock(ssh, body.seconds)
    except ScreenLockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "slate.screen_lock.auto_lock_set",
        username=user.username, seconds=body.seconds,
    )
    return _screen_lock_to_dict(st)
