"""Ambient WiFi scan API — UI control for the Q2-A background scan loop.

Endpoints are device-scoped (the active device comes from the
``get_device_connections`` dep). Bands are restricted to "2" / "5" / "6".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.db.models import AmbientScanConfigRow, ScanHistoryRow
from app.devices.registry import DeviceConnections
from app.scheduler.ambient_scan import AmbientScanManager
from app.wifi.models import WifiBand

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi/ambient", tags=["wifi", "ambient"])


BandLit = Literal["2", "5", "6"]
_BANDS: tuple[BandLit, ...] = ("2", "5", "6")


class AmbientConfigView(BaseModel):
    band: BandLit
    enabled: bool
    interval_s: int
    retention_days: int
    last_run_at: datetime | None
    last_status: str
    last_error: str
    # Live stats computed on read so the UI doesn't need a second roundtrip.
    persisted_scans_24h: int
    persisted_scans_total: int


class AmbientConfigUpsert(BaseModel):
    enabled: bool
    interval_s: int = Field(default=60, ge=30, le=3600)
    retention_days: int = Field(default=7, ge=1, le=90)


class RunNowResult(BaseModel):
    status: str
    scan_id: int | None = None
    neighbors: int | None = None


def _manager(request: Request) -> AmbientScanManager:
    mgr = getattr(request.app.state, "ambient_scan_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ambient scan manager not initialised",
        )
    return mgr


async def _row_or_defaults(
    sf: async_sessionmaker, slug: str, band: BandLit,
) -> tuple[AmbientScanConfigRow | None, AmbientConfigUpsert]:
    """Return (row, view-default) — defaults for bands with no row yet."""
    async with sf() as s:
        row = await s.scalar(
            select(AmbientScanConfigRow).where(
                AmbientScanConfigRow.device_slug == slug,
                AmbientScanConfigRow.band == band,
            ),
        )
    defaults = AmbientConfigUpsert(enabled=False)
    return row, defaults


async def _stats_for(
    sf: async_sessionmaker, slug: str, band: BandLit,
) -> tuple[int, int]:
    """Return (count_last_24h, count_total) of ambient scans for this band."""
    cutoff = datetime.utcnow().replace(microsecond=0)
    import datetime as _d
    cutoff = cutoff - _d.timedelta(hours=24)
    async with sf() as s:
        total = await s.scalar(
            select(func.count())
            .select_from(ScanHistoryRow)
            .where(
                ScanHistoryRow.device_slug == slug,
                ScanHistoryRow.band == band,
                ScanHistoryRow.source == "ambient",
            ),
        )
        last_24h = await s.scalar(
            select(func.count())
            .select_from(ScanHistoryRow)
            .where(
                ScanHistoryRow.device_slug == slug,
                ScanHistoryRow.band == band,
                ScanHistoryRow.source == "ambient",
                ScanHistoryRow.started_at >= cutoff,
            ),
        )
    return int(last_24h or 0), int(total or 0)


def _build_view(
    band: BandLit,
    row: AmbientScanConfigRow | None,
    last_24h: int,
    total: int,
) -> AmbientConfigView:
    if row is None:
        return AmbientConfigView(
            band=band, enabled=False, interval_s=60, retention_days=7,
            last_run_at=None, last_status="", last_error="",
            persisted_scans_24h=last_24h, persisted_scans_total=total,
        )
    return AmbientConfigView(
        band=band,
        enabled=row.enabled,
        interval_s=row.interval_s,
        retention_days=row.retention_days,
        last_run_at=row.last_run_at,
        last_status=row.last_status,
        last_error=row.last_error,
        persisted_scans_24h=last_24h,
        persisted_scans_total=total,
    )


@router.get("", response_model=list[AmbientConfigView])
async def list_ambient_configs(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[AmbientConfigView]:
    """Return one row per band (with defaults if no config exists yet)."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    out: list[AmbientConfigView] = []
    for band in _BANDS:
        row, _ = await _row_or_defaults(sf, conn.slug, band)
        last_24h, total = await _stats_for(sf, conn.slug, band)
        out.append(_build_view(band, row, last_24h, total))
    return out


@router.put("/{band}", response_model=AmbientConfigView)
async def upsert_ambient_config(
    band: BandLit,
    body: AmbientConfigUpsert,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> AmbientConfigView:
    """Create or update the ambient config for one band, then reconfigure
    the scheduler accordingly."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(AmbientScanConfigRow).where(
                AmbientScanConfigRow.device_slug == conn.slug,
                AmbientScanConfigRow.band == band,
            ),
        )
        if row is None:
            row = AmbientScanConfigRow(
                device_slug=conn.slug, band=band,
                enabled=body.enabled,
                interval_s=body.interval_s,
                retention_days=body.retention_days,
            )
            s.add(row)
        else:
            row.enabled = body.enabled
            row.interval_s = body.interval_s
            row.retention_days = body.retention_days
        await s.commit()
        await s.refresh(row)

    # Reconfigure the running scheduler (idempotent).
    _manager(request).reconfigure(
        conn.slug, band,
        enabled=body.enabled, interval_s=body.interval_s,
    )

    last_24h, total = await _stats_for(sf, conn.slug, band)
    logger.info(
        "wifi.ambient.config.upsert",
        username=user.username, device=conn.slug, band=band,
        enabled=body.enabled, interval_s=body.interval_s,
    )
    return _build_view(band, row, last_24h, total)


@router.post("/{band}/run-now", response_model=RunNowResult)
async def run_ambient_now(
    band: BandLit,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> RunNowResult:
    """Trigger one ambient pass synchronously — useful to validate config
    without waiting for the next scheduler tick."""
    logger.info(
        "wifi.ambient.run_now",
        username=user.username, device=conn.slug, band=band,
    )
    res = await _manager(request).run_now(conn.slug, band)
    return RunNowResult(**res)


class RecentAmbientScan(BaseModel):
    id: int
    band: BandLit
    started_at: datetime
    neighbors_count: int
    duration_s: float


@router.get("/recent", response_model=list[RecentAmbientScan])
async def list_recent_ambient_scans(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = 20,
) -> list[RecentAmbientScan]:
    """Quick read of the latest ambient scan_history rows for this device."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        rows = (await s.scalars(
            select(ScanHistoryRow)
            .where(
                ScanHistoryRow.device_slug == conn.slug,
                ScanHistoryRow.source == "ambient",
            )
            .order_by(desc(ScanHistoryRow.started_at))
            .limit(max(1, min(limit, 100))),
        )).all()
    return [
        RecentAmbientScan(
            id=r.id, band=r.band, started_at=r.started_at,
            neighbors_count=r.neighbors_count, duration_s=r.duration_s,
        )
        for r in rows
    ]
