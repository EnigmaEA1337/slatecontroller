"""Radio (layer-1) endpoints — channel/htmode/txpower/country + scanner.

Two surfaces :
  - ``GET / PUT /api/wifi/radios``       per-device band configs
  - ``POST /api/wifi/radios/{band}/scan`` trigger a live scan + scoring
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections, get_slate_ssh
from app.auth import User, get_current_user
from app.db.models import ThreatEventRow
from app.devices.registry import DeviceConnections
from app.slate.ssh import SlateSSH
from app.wifi.models import WifiBand
from app.wifi.radio_config import (
    DEFAULT_HTMODE,
    HTMODE_BY_BAND,
    RadioConfig,
    RadioConfigStore,
)
from app.wifi.scanner import (
    NeighborAP,
    ScanResult,
    ThreatEvent,
    detect_threats,
    scan_band,
)
from app.wifi.store import WifiSsidStore

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi/radios", tags=["wifi", "radio"])


# ---------------------------- Pydantic views ---------------------------- #

class RadioConfigView(BaseModel):
    band: WifiBand
    channel: int
    htmode: str
    txpower_percent: int
    country: str
    available_htmodes: list[str]

    @classmethod
    def from_config(cls, c: RadioConfig) -> "RadioConfigView":
        return cls(
            band=c.band,
            channel=c.channel,
            htmode=c.htmode or DEFAULT_HTMODE[c.band],
            txpower_percent=c.txpower_percent,
            country=c.country,
            available_htmodes=list(HTMODE_BY_BAND[c.band]),
        )


class RadioConfigsResponse(BaseModel):
    device_slug: str
    bands: dict[str, RadioConfigView]


class RadioConfigPatch(BaseModel):
    channel: int | None = Field(default=None, ge=0, le=233)
    htmode: str | None = None
    txpower_percent: int | None = Field(default=None, ge=10, le=100)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class NeighborAPView(BaseModel):
    bssid: str
    ssid: str
    hidden: bool
    channel: int
    band: WifiBand
    rssi_dbm: int
    security: str
    ht_mode: str
    is_wps_enabled: bool
    is_ours: bool

    @classmethod
    def from_neighbor(cls, n: NeighborAP) -> "NeighborAPView":
        return cls(
            bssid=n.bssid, ssid=n.ssid, hidden=n.hidden,
            channel=n.channel, band=n.band, rssi_dbm=n.rssi_dbm,
            security=n.security, ht_mode=n.ht_mode,
            is_wps_enabled=n.is_wps_enabled, is_ours=n.is_ours,
        )


class ChannelScoreView(BaseModel):
    band: WifiBand
    channel: int
    score: int
    neighbor_count: int
    is_dfs: bool
    is_psc: bool
    is_current: bool
    reasons: list[str]


class ThreatEventView(BaseModel):
    kind: str
    level: str
    bssid: str
    ssid: str
    channel: int
    rssi_dbm: int
    message: str

    @classmethod
    def from_event(cls, t: ThreatEvent) -> "ThreatEventView":
        return cls(
            kind=t.kind, level=t.level, bssid=t.bssid, ssid=t.ssid,
            channel=t.channel, rssi_dbm=t.rssi_dbm, message=t.message,
        )


class ScanResponse(BaseModel):
    band: WifiBand
    iface: str
    duration_s: float
    started_at: float
    neighbors: list[NeighborAPView]
    channel_scores: list[ChannelScoreView]
    recommended_channel: int | None
    current_channel: int | None
    threats: list[ThreatEventView]


# ---------------------------- helpers ---------------------------- #

def _store(request: Request) -> RadioConfigStore:
    sf: async_sessionmaker = request.app.state.db_session_factory
    return RadioConfigStore(sf)


def _wifi_store(request: Request) -> WifiSsidStore:
    sf: async_sessionmaker = request.app.state.db_session_factory
    return WifiSsidStore(sf)


# ---------------------------- routes : config ---------------------------- #

@router.get("", response_model=RadioConfigsResponse)
async def get_all_radios(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> RadioConfigsResponse:
    """Return the per-band config for the active device."""
    store = _store(request)
    configs = await store.get_all_for_device(conn.slug)
    return RadioConfigsResponse(
        device_slug=conn.slug,
        bands={
            b: RadioConfigView.from_config(configs[b])  # type: ignore[index]
            for b in ("2", "5", "6")
        },
    )


@router.put("/{band}", response_model=RadioConfigView)
async def update_radio(
    band: WifiBand,
    body: RadioConfigPatch,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> RadioConfigView:
    """Patch one band's config (partial — None fields unchanged)."""
    if body.htmode is not None and body.htmode not in HTMODE_BY_BAND[band]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"htmode {body.htmode!r} non supporté sur {band} GHz — "
                f"valeurs autorisées : {', '.join(HTMODE_BY_BAND[band])}"
            ),
        )
    store = _store(request)
    updated = await store.upsert(
        conn.slug, band,
        channel=body.channel,
        htmode=body.htmode,
        txpower_percent=body.txpower_percent,
        country=body.country,
    )
    logger.info(
        "wifi.radio.updated",
        username=user.username, device=conn.slug, band=band,
        channel=updated.channel, htmode=updated.htmode,
        txpower=updated.txpower_percent, country=updated.country,
    )
    return RadioConfigView.from_config(updated)


# ---------------------------- routes : scanner ---------------------------- #

@router.post("/{band}/scan", response_model=ScanResponse)
async def scan_radio(
    band: WifiBand,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
    iface: Annotated[str | None, Query(description="Iface override")] = None,
) -> ScanResponse:
    """Run a live scan on the given band and return AP list + channel scores.

    Slow-ish (~15-25s for 5 GHz with DFS) — clients should show a
    progress indicator. The route is not rate-limited but a single
    sustained scan per band per minute is the operator-friendly cap.
    """
    try:
        result: ScanResult = await scan_band(ssh, band, iface=iface)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc

    # Augment with threat detection : we need OUR ssid set + bssids to
    # exclude ourselves from the analysis. The ssid catalog provides the
    # human-friendly names ; the BSSIDs come from the live iface set.
    ssid_store = _wifi_store(request)
    catalog = await ssid_store.list_all()
    our_ssids = {s.ssid_name.strip().lower() for s in catalog if s.ssid_name}

    # Best-effort BSSID dump via SSH (cheap, ~50ms).
    our_bssids: set[str] = set()
    try:
        info = await ssh.run(
            "for ifn in $(iw dev | grep Interface | awk '{print $2}'); do "
            "  iw dev $ifn info 2>/dev/null | "
            "    grep addr | awk '{print $2}' | tr A-Z a-z; "
            "done",
            timeout=10,
        )
        for line in info.stdout.splitlines():
            line = line.strip()
            if line and len(line) == 17:
                our_bssids.add(line)
    except Exception:  # noqa: BLE001
        pass

    our_channels: dict[WifiBand, int] = {}
    if result.current_channel:
        our_channels[band] = result.current_channel

    threats = detect_threats(
        result.neighbors,
        our_ssids=our_ssids,
        our_bssids=our_bssids,
        our_channels=our_channels,
    )

    # Persist threats so AUDIT/Air Watch keeps a timeline. Best-effort —
    # we don't fail the scan if the DB write trips.
    try:
        sf: async_sessionmaker = request.app.state.db_session_factory
        from datetime import UTC as _UTC, datetime as _dt
        async with sf() as s:
            for t in threats:
                existing = await s.scalar(
                    select(ThreatEventRow).where(
                        ThreatEventRow.device_slug == conn.slug,
                        ThreatEventRow.kind == t.kind,
                        ThreatEventRow.bssid == t.bssid,
                    ),
                )
                if existing is None:
                    s.add(ThreatEventRow(
                        device_slug=conn.slug,
                        kind=t.kind, level=t.level,
                        bssid=t.bssid, ssid=t.ssid,
                        channel=t.channel, rssi_dbm=t.rssi_dbm,
                        message=t.message,
                    ))
                else:
                    existing.last_seen_at = _dt.now(_UTC)
                    existing.rssi_dbm = t.rssi_dbm
                    existing.message = t.message
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("wifi.scan.threat_persist_failed", error=str(exc))

    logger.info(
        "wifi.scan",
        username=user.username, device=conn.slug,
        band=band, neighbors=len(result.neighbors),
        threats=len(threats), duration_s=round(result.duration_s, 2),
    )

    return ScanResponse(
        band=result.band,
        iface=result.iface,
        duration_s=result.duration_s,
        started_at=result.started_at,
        neighbors=[NeighborAPView.from_neighbor(n) for n in result.neighbors],
        channel_scores=[
            ChannelScoreView(
                band=s.band, channel=s.channel, score=s.score,
                neighbor_count=s.neighbor_count, is_dfs=s.is_dfs, is_psc=s.is_psc,
                is_current=s.is_current, reasons=s.reasons,
            )
            for s in result.channel_scores
        ],
        recommended_channel=result.recommended_channel,
        current_channel=result.current_channel,
        threats=[ThreatEventView.from_event(t) for t in threats],
    )
