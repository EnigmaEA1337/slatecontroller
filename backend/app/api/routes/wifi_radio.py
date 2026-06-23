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
from app.db.models import (
    ApReviewRow,
    BssidReviewRow,
    ScanHistoryRow,
    ScanNeighborRow,
    ThreatEventRow,
)
from app.devices.locations import DeviceLocationStore
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
    group_by_physical_ap,
    scan_band,
    scan_band_extended,
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
    vendor: str
    vendor_slug: str
    is_randomized: bool
    ap_root: str
    # review_status_own : explicit per-BSSID override (None if absent).
    # review_status_effective : own override if set, else the group's
    #     status, else None. UI renders the effective one (italic when
    #     inherited from the group, plain when own).
    review_status_own: str | None = None
    review_status_effective: str | None = None
    review_label_own: str = ""
    # Multi-pass stats (single-pass scans : seen_count=1, both extrema
    # equal rssi_dbm, offsets=0).
    seen_count: int = 1
    rssi_max: int = 0
    rssi_min: int = 0
    first_seen_offset_s: float = 0.0
    last_seen_offset_s: float = 0.0

    @classmethod
    def from_neighbor(cls, n: NeighborAP) -> "NeighborAPView":
        return cls(
            bssid=n.bssid, ssid=n.ssid, hidden=n.hidden,
            channel=n.channel, band=n.band, rssi_dbm=n.rssi_dbm,
            security=n.security, ht_mode=n.ht_mode,
            is_wps_enabled=n.is_wps_enabled, is_ours=n.is_ours,
            vendor=n.vendor, vendor_slug=n.vendor_slug,
            is_randomized=n.is_randomized,
            ap_root=n.ap_root,
            seen_count=n.seen_count,
            rssi_max=n.rssi_max or n.rssi_dbm,
            rssi_min=n.rssi_min or n.rssi_dbm,
            first_seen_offset_s=n.first_seen_offset_s,
            last_seen_offset_s=n.last_seen_offset_s,
        )


class PhysicalAPGroupView(BaseModel):
    """Cluster of VAPs that share a physical AP box."""

    ap_root: str
    # Primary channel = strongest member's channel. Kept for callers
    # (AP review modal, channel scoring) that need a single representative
    # value ; the UI uses ``channels`` for the multi-radio aggregate
    # display.
    channel: int
    channels: list[int]
    bands: list[str]
    rssi_dbm: int
    vendor: str
    vendor_slug: str
    is_all_randomized: bool
    has_wps: bool
    ssids: list[str]
    hidden_count: int
    member_count: int
    bssids: list[str]
    # None when there's no review row yet (implicit "unknown" state).
    review_status: str | None = None
    review_label: str = ""


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
    physical_aps: list[PhysicalAPGroupView]


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
    override_lat: Annotated[
        float | None,
        Query(alias="lat", description="Override location lat (else uses device current)", ge=-90, le=90),
    ] = None,
    override_lon: Annotated[
        float | None,
        Query(alias="lon", description="Override location lon", ge=-180, le=180),
    ] = None,
    override_accuracy_m: Annotated[
        float | None,
        Query(alias="accuracy_m", description="Override accuracy in metres", ge=0),
    ] = None,
    override_source: Annotated[
        str | None,
        Query(alias="source", description="Override location source label"),
    ] = None,
    duration_s: Annotated[
        int,
        Query(
            ge=0, le=1200,
            description=(
                "Multi-pass scan : total wall-clock budget in seconds. "
                "0 (default) = single iw-scan pass (~3s on 2.4 GHz, "
                "~25s on 5 GHz). > 0 loops scans, merging by BSSID."
            ),
        ),
    ] = 0,
) -> ScanResponse:
    """Run a live scan on the given band and return AP list + channel scores.

    Single-pass : slow-ish (~15-25s for 5 GHz with DFS). Multi-pass
    (``duration_s > 0``) : loops until the time budget is spent,
    merging observations by BSSID and exposing per-BSSID stats
    (``seen_count``, ``rssi_max/min``, ``first/last_seen_offset_s``).
    Hard cap at 20 minutes to avoid runaway sessions ; for longer
    monitoring use the surveillance-session endpoints (planned).
    """
    try:
        if duration_s > 0:
            result: ScanResult = await scan_band_extended(
                ssh, band, total_duration_s=duration_s, iface=iface,
            )
        else:
            result = await scan_band(ssh, band, iface=iface)
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

    # Fetch operator reviews once — used both to suppress Air Watch
    # false positives and to overlay status badges on physical_aps +
    # individual neighbours below. Two layers : group (ap_root) and
    # per-BSSID override.
    reviews_by_root: dict[str, tuple[str, str]] = {}
    reviews_by_bssid: dict[str, tuple[str, str]] = {}
    try:
        sf_reviews: async_sessionmaker = request.app.state.db_session_factory
        async with sf_reviews() as s_rev:
            ap_rows = (await s_rev.scalars(
                select(ApReviewRow).where(
                    ApReviewRow.device_slug == conn.slug,
                ),
            )).all()
            bs_rows = (await s_rev.scalars(
                select(BssidReviewRow).where(
                    BssidReviewRow.device_slug == conn.slug,
                ),
            )).all()
        reviews_by_root = {r.ap_root: (r.status, r.label) for r in ap_rows}
        reviews_by_bssid = {
            r.bssid.lower(): (r.status, r.label) for r in bs_rows
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("wifi.scan.reviews_overlay_failed", error=str(exc))

    bssid_to_root = {n.bssid: n.ap_root for n in result.neighbors}

    def effective_for_bssid(bssid: str) -> str | None:
        """Per-BSSID override wins over the group's status."""
        own = reviews_by_bssid.get(bssid.lower())
        if own is not None:
            return own[0]
        root = bssid_to_root.get(bssid, "")
        grp = reviews_by_root.get(root)
        return grp[0] if grp else None

    raw_threats = detect_threats(
        result.neighbors,
        our_ssids=our_ssids,
        our_bssids=our_bssids,
        our_channels=our_channels,
    )
    # Drop threats whose BSSID has an effective "trusted" status (own
    # override OR inherited from the group). legacy_crypto / wps_enabled
    # stay — they're crypto facts about the radio, trust doesn't fix
    # them.
    SUPPRESSIBLE = {"evil_twin", "strong_neighbor"}
    threats = [
        t for t in raw_threats
        if not (
            t.kind in SUPPRESSIBLE
            and effective_for_bssid(t.bssid) == "trusted"
        )
    ]

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

    # Resolve the geolocation context for this scan run.
    #   1. Explicit query-string override wins (caller supplied a one-shot
    #      lat/lon — eg. the operator pinned a manual position before
    #      clicking SCAN).
    #   2. Otherwise fall back to the device's CURRENT location entry
    #      (most-recent row in `device_locations`).
    #   3. Otherwise no geo stamp (lat/lon = None).
    scan_lat: float | None = None
    scan_lon: float | None = None
    scan_accuracy: float | None = None
    scan_source: str = ""
    if override_lat is not None and override_lon is not None:
        scan_lat = override_lat
        scan_lon = override_lon
        scan_accuracy = override_accuracy_m
        scan_source = override_source or "manual"
    else:
        sf: async_sessionmaker = request.app.state.db_session_factory
        loc_store = DeviceLocationStore(sf)
        cur_loc = await loc_store.current(conn.slug)
        if cur_loc is not None:
            scan_lat = cur_loc.lat
            scan_lon = cur_loc.lon
            scan_accuracy = cur_loc.accuracy_m
            scan_source = cur_loc.source

    # Persist scan run + its neighbour list so the History tab and the
    # map view have data to render. Best-effort — failure here doesn't
    # fail the user-visible scan.
    try:
        sf: async_sessionmaker = request.app.state.db_session_factory
        from datetime import UTC as _UTC, datetime as _dt
        async with sf() as s:
            run = ScanHistoryRow(
                device_slug=conn.slug,
                band=band, iface=result.iface,
                started_at=_dt.fromtimestamp(result.started_at, tz=_UTC),
                duration_s=result.duration_s,
                lat=scan_lat, lon=scan_lon, accuracy_m=scan_accuracy,
                source=scan_source,
                neighbors_count=len(result.neighbors),
                threats_count=len(threats),
                recommended_channel=result.recommended_channel,
                current_channel=result.current_channel,
            )
            s.add(run)
            await s.flush()
            for n in result.neighbors:
                s.add(ScanNeighborRow(
                    scan_id=run.id, bssid=n.bssid, ssid=n.ssid,
                    hidden=n.hidden, channel=n.channel, band=n.band,
                    rssi_dbm=n.rssi_dbm, security=n.security,
                    ht_mode=n.ht_mode, is_wps_enabled=n.is_wps_enabled,
                    ap_root=n.ap_root, vendor=n.vendor,
                    vendor_slug=n.vendor_slug,
                    is_randomized=n.is_randomized,
                    seen_count=n.seen_count,
                    rssi_max=n.rssi_max or n.rssi_dbm,
                    rssi_min=n.rssi_min or n.rssi_dbm,
                    first_seen_offset_s=n.first_seen_offset_s,
                    last_seen_offset_s=n.last_seen_offset_s,
                ))
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("wifi.scan.history_persist_failed", error=str(exc))

    logger.info(
        "wifi.scan",
        username=user.username, device=conn.slug,
        band=band, neighbors=len(result.neighbors),
        threats=len(threats), duration_s=round(result.duration_s, 2),
        lat=scan_lat, lon=scan_lon, source=scan_source,
    )

    physical_aps = group_by_physical_ap(result.neighbors)

    def neighbour_view_with_reviews(n: NeighborAP) -> NeighborAPView:
        v = NeighborAPView.from_neighbor(n)
        own = reviews_by_bssid.get(n.bssid.lower())
        if own is not None:
            v.review_status_own = own[0]
            v.review_label_own = own[1]
            v.review_status_effective = own[0]
        else:
            grp = reviews_by_root.get(n.ap_root)
            v.review_status_effective = grp[0] if grp else None
        return v

    return ScanResponse(
        band=result.band,
        iface=result.iface,
        duration_s=result.duration_s,
        started_at=result.started_at,
        neighbors=[neighbour_view_with_reviews(n) for n in result.neighbors],
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
        physical_aps=[
            PhysicalAPGroupView(
                ap_root=g.ap_root, channel=g.channel,
                channels=list(g.channels), bands=list(g.bands),
                rssi_dbm=g.rssi_dbm,
                vendor=g.vendor, vendor_slug=g.vendor_slug,
                is_all_randomized=g.is_all_randomized, has_wps=g.has_wps,
                ssids=g.ssids, hidden_count=g.hidden_count,
                member_count=len(g.members),
                bssids=[m.bssid for m in g.members],
                review_status=reviews_by_root.get(g.ap_root, (None, ""))[0],
                review_label=reviews_by_root.get(g.ap_root, (None, ""))[1],
            )
            for g in physical_aps
        ],
    )
