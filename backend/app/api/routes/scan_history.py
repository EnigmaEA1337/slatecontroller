"""Scan history endpoints — list past scans + their neighbour list.

The history is per-device. Persistence is done by the scan route in
``wifi_radio.py``. This module is read-only ; the only write is delete.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.db.models import ApReviewRow, BssidReviewRow, ScanHistoryRow, ScanNeighborRow
from app.devices.registry import DeviceConnections
from app.wifi.scanner import NeighborAP, group_by_physical_ap, score_channels
from app.wifi.models import WifiBand

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi/scan-history", tags=["wifi", "scan-history"])


class ScanHistoryView(BaseModel):
    id: int
    device_slug: str
    band: str
    iface: str
    started_at: datetime
    duration_s: float
    lat: float | None
    lon: float | None
    accuracy_m: float | None
    source: str
    neighbors_count: int
    threats_count: int
    recommended_channel: int | None
    current_channel: int | None
    note: str


class ScanNeighborView(BaseModel):
    bssid: str
    ssid: str
    hidden: bool
    channel: int
    band: str
    rssi_dbm: int
    security: str
    ht_mode: str
    is_wps_enabled: bool
    ap_root: str
    vendor: str
    vendor_slug: str
    is_randomized: bool
    review_status_own: str | None = None
    review_status_effective: str | None = None
    review_label_own: str = ""
    seen_count: int = 1
    rssi_max: int = 0
    rssi_min: int = 0
    first_seen_offset_s: float = 0.0
    last_seen_offset_s: float = 0.0


class ChannelScoreView(BaseModel):
    band: str
    channel: int
    score: int
    neighbor_count: int
    is_dfs: bool
    is_psc: bool
    is_current: bool
    reasons: list[str]


class PhysicalAPGroupView(BaseModel):
    ap_root: str
    channel: int
    rssi_dbm: int
    vendor: str
    vendor_slug: str
    is_all_randomized: bool
    has_wps: bool
    ssids: list[str]
    hidden_count: int
    member_count: int
    bssids: list[str]
    review_status: str | None = None
    review_label: str = ""


class ScanHistoryDetailView(ScanHistoryView):
    neighbors: list[ScanNeighborView]
    channel_scores: list[ChannelScoreView]
    physical_aps: list[PhysicalAPGroupView]


@router.get("", response_model=list[ScanHistoryView])
async def list_history(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
    band: Annotated[str | None, Query(description="Filter by band 2/5/6")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ScanHistoryView]:
    """Return scans for the active device, newest first."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        q = (
            select(ScanHistoryRow)
            .where(ScanHistoryRow.device_slug == conn.slug)
            .order_by(desc(ScanHistoryRow.started_at))
            .limit(limit)
        )
        if band is not None:
            q = q.where(ScanHistoryRow.band == band)
        rows = (await s.scalars(q)).all()
    return [_row_to_view(r) for r in rows]


@router.get("/{scan_id}", response_model=ScanHistoryDetailView)
async def get_history(
    scan_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ScanHistoryDetailView:
    """Return one scan with its full neighbour list."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        run = await s.scalar(
            select(ScanHistoryRow).where(
                ScanHistoryRow.id == scan_id,
                ScanHistoryRow.device_slug == conn.slug,
            ),
        )
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scan {scan_id} not found",
            )
        neighbors = (await s.scalars(
            select(ScanNeighborRow)
            .where(ScanNeighborRow.scan_id == scan_id)
            .order_by(desc(ScanNeighborRow.rssi_dbm)),
        )).all()
    base = _row_to_view(run)
    # Recompute channel scoring from the persisted neighbours so the
    # heat-map is restored when a past scan is re-opened. Deterministic :
    # same neighbours + same band + same current_channel => same scores.
    band_typed: WifiBand = run.band  # type: ignore[assignment]
    rebuilt_neighbors = [
        NeighborAP(
            bssid=n.bssid, ssid=n.ssid, hidden=n.hidden,
            channel=n.channel, band=n.band,  # type: ignore[arg-type]
            rssi_dbm=n.rssi_dbm, security=n.security, ht_mode=n.ht_mode,
            is_wps_enabled=n.is_wps_enabled,
            vendor=n.vendor, vendor_slug=n.vendor_slug,
            is_randomized=n.is_randomized, ap_root=n.ap_root,
        )
        for n in neighbors
    ]
    scores = score_channels(
        band_typed, rebuilt_neighbors, current_channel=run.current_channel,
    )
    # Reconstruct physical AP groups from the persisted neighbours, then
    # overlay the operator's current review state on each one so the
    # reloaded scan stays in sync with the latest review decisions.
    groups = group_by_physical_ap(rebuilt_neighbors)
    async with sf() as s:
        review_rows = (await s.scalars(
            select(ApReviewRow).where(ApReviewRow.device_slug == conn.slug),
        )).all()
        bssid_review_rows = (await s.scalars(
            select(BssidReviewRow).where(
                BssidReviewRow.device_slug == conn.slug,
            ),
        )).all()
    reviews_by_root = {r.ap_root: (r.status, r.label) for r in review_rows}
    reviews_by_bssid = {
        r.bssid.lower(): (r.status, r.label) for r in bssid_review_rows
    }

    def _neighbor_view(n: ScanNeighborRow) -> ScanNeighborView:
        own = reviews_by_bssid.get(n.bssid.lower())
        if own is not None:
            own_status, own_label = own
            effective = own_status
        else:
            own_status = None
            own_label = ""
            grp = reviews_by_root.get(n.ap_root)
            effective = grp[0] if grp else None
        return ScanNeighborView(
            bssid=n.bssid, ssid=n.ssid, hidden=n.hidden,
            channel=n.channel, band=n.band, rssi_dbm=n.rssi_dbm,
            security=n.security, ht_mode=n.ht_mode,
            is_wps_enabled=n.is_wps_enabled,
            ap_root=n.ap_root, vendor=n.vendor,
            vendor_slug=n.vendor_slug,
            is_randomized=n.is_randomized,
            review_status_own=own_status,
            review_status_effective=effective,
            review_label_own=own_label,
            seen_count=n.seen_count or 1,
            rssi_max=n.rssi_max if n.rssi_max != -100 else n.rssi_dbm,
            rssi_min=n.rssi_min if n.rssi_min != -100 else n.rssi_dbm,
            first_seen_offset_s=n.first_seen_offset_s or 0.0,
            last_seen_offset_s=n.last_seen_offset_s or 0.0,
        )

    return ScanHistoryDetailView(
        **base.model_dump(),
        neighbors=[_neighbor_view(n) for n in neighbors],
        channel_scores=[
            ChannelScoreView(
                band=s.band, channel=s.channel, score=s.score,
                neighbor_count=s.neighbor_count,
                is_dfs=s.is_dfs, is_psc=s.is_psc,
                is_current=s.is_current, reasons=list(s.reasons),
            )
            for s in scores
        ],
        physical_aps=[
            PhysicalAPGroupView(
                ap_root=g.ap_root, channel=g.channel, rssi_dbm=g.rssi_dbm,
                vendor=g.vendor, vendor_slug=g.vendor_slug,
                is_all_randomized=g.is_all_randomized, has_wps=g.has_wps,
                ssids=g.ssids, hidden_count=g.hidden_count,
                member_count=len(g.members),
                bssids=[m.bssid for m in g.members],
                review_status=reviews_by_root.get(g.ap_root, (None, ""))[0],
                review_label=reviews_by_root.get(g.ap_root, (None, ""))[1],
            )
            for g in groups
        ],
    )


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_history(
    scan_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Drop one scan + its neighbours (cascade)."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        run = await s.scalar(
            select(ScanHistoryRow).where(
                ScanHistoryRow.id == scan_id,
                ScanHistoryRow.device_slug == conn.slug,
            ),
        )
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scan {scan_id} not found",
            )
        await s.delete(run)
        await s.commit()
    logger.info(
        "wifi.scan_history.deleted",
        username=user.username, device=conn.slug, scan_id=scan_id,
    )


def _row_to_view(r: ScanHistoryRow) -> ScanHistoryView:
    return ScanHistoryView(
        id=r.id, device_slug=r.device_slug, band=r.band, iface=r.iface,
        started_at=r.started_at, duration_s=r.duration_s,
        lat=r.lat, lon=r.lon, accuracy_m=r.accuracy_m, source=r.source,
        neighbors_count=r.neighbors_count, threats_count=r.threats_count,
        recommended_channel=r.recommended_channel,
        current_channel=r.current_channel, note=r.note,
    )
