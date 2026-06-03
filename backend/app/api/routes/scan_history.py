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
from app.db.models import ScanHistoryRow, ScanNeighborRow
from app.devices.registry import DeviceConnections

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


class ScanHistoryDetailView(ScanHistoryView):
    neighbors: list[ScanNeighborView]


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
    return ScanHistoryDetailView(
        **base.model_dump(),
        neighbors=[
            ScanNeighborView(
                bssid=n.bssid, ssid=n.ssid, hidden=n.hidden,
                channel=n.channel, band=n.band, rssi_dbm=n.rssi_dbm,
                security=n.security, ht_mode=n.ht_mode,
                is_wps_enabled=n.is_wps_enabled,
                ap_root=n.ap_root, vendor=n.vendor,
                vendor_slug=n.vendor_slug,
                is_randomized=n.is_randomized,
            )
            for n in neighbors
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
