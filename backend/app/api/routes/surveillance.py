"""Surveillance session API (Q2-C).

Endpoints :

  GET    /api/wifi/surveillance              list sessions for active device
  POST   /api/wifi/surveillance              create + start a new session
  GET    /api/wifi/surveillance/{id}         session detail
  POST   /api/wifi/surveillance/{id}/cancel  stop early
  DELETE /api/wifi/surveillance/{id}         delete (cascades to scans)
  GET    /api/wifi/surveillance/{id}/timeline   per-BSSID classified timeline
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.db.models import ScanHistoryRow, SurveillanceSessionRow
from app.devices.registry import DeviceConnections
from app.scheduler.surveillance import SurveillanceManager

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi/surveillance", tags=["wifi", "surveillance"])


class SurveillanceSessionView(BaseModel):
    id: int
    name: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    target_duration_s: int
    interval_s: int
    bands: str
    location_lat: float | None
    location_lon: float | None
    location_label: str
    note: str
    total_passes: int
    unique_bssids: int


class SurveillanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    bands: str = Field(
        default="5",
        description="CSV des bandes, ex: '5' / '2,5' / '2,5,6'",
        max_length=8,
    )
    target_duration_s: int = Field(
        ge=60, le=86400,
        description="Durée totale en secondes (1min — 24h)",
    )
    interval_s: int = Field(
        default=60, ge=30, le=3600,
        description="Intervalle entre passes (30s min, DFS-safe)",
    )
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)
    location_label: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=1024)


def _manager(request: Request) -> SurveillanceManager:
    mgr = getattr(request.app.state, "surveillance_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="surveillance manager not initialised",
        )
    return mgr


def _row_to_view(r: SurveillanceSessionRow) -> SurveillanceSessionView:
    return SurveillanceSessionView(
        id=r.id, name=r.name, status=r.status,
        started_at=r.started_at, ended_at=r.ended_at,
        target_duration_s=r.target_duration_s,
        interval_s=r.interval_s, bands=r.bands,
        location_lat=r.location_lat, location_lon=r.location_lon,
        location_label=r.location_label, note=r.note,
        total_passes=r.total_passes, unique_bssids=r.unique_bssids,
    )


@router.get("", response_model=list[SurveillanceSessionView])
async def list_sessions(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[SurveillanceSessionView]:
    """List all sessions for the active device, newest first."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        rows = (await s.scalars(
            select(SurveillanceSessionRow)
            .where(SurveillanceSessionRow.device_slug == conn.slug)
            .order_by(desc(SurveillanceSessionRow.started_at)),
        )).all()
    return [_row_to_view(r) for r in rows]


@router.post("", response_model=SurveillanceSessionView)
async def create_session(
    body: SurveillanceCreate,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> SurveillanceSessionView:
    """Create + start a new surveillance session."""
    try:
        row = await _manager(request).start_session(
            slug=conn.slug,
            name=body.name,
            bands_csv=body.bands,
            target_duration_s=body.target_duration_s,
            interval_s=body.interval_s,
            location_lat=body.location_lat,
            location_lon=body.location_lon,
            location_label=body.location_label,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        ) from exc
    logger.info(
        "wifi.surveillance.created",
        username=user.username, device=conn.slug,
        session_id=row.id, duration_s=body.target_duration_s,
    )
    return _row_to_view(row)


@router.get("/{session_id}", response_model=SurveillanceSessionView)
async def get_session(
    session_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> SurveillanceSessionView:
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(SurveillanceSessionRow).where(
                SurveillanceSessionRow.id == session_id,
                SurveillanceSessionRow.device_slug == conn.slug,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"session {session_id} not found",
            )
    return _row_to_view(row)


@router.post("/{session_id}/cancel", response_model=SurveillanceSessionView)
async def cancel_session(
    session_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> SurveillanceSessionView:
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(SurveillanceSessionRow).where(
                SurveillanceSessionRow.id == session_id,
                SurveillanceSessionRow.device_slug == conn.slug,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"session {session_id} not found",
            )
    await _manager(request).cancel_session(session_id)
    sf: async_sessionmaker = request.app.state.db_session_factory  # noqa: F811
    async with sf() as s:
        row = await s.get(SurveillanceSessionRow, session_id)
    logger.info(
        "wifi.surveillance.cancelled",
        username=user.username, device=conn.slug, session_id=session_id,
    )
    return _row_to_view(row)  # type: ignore[arg-type]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Drop a session AND the scan_history rows linked to it. Use with
    care — manual scans never have a session_id so they're not at risk."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(SurveillanceSessionRow).where(
                SurveillanceSessionRow.id == session_id,
                SurveillanceSessionRow.device_slug == conn.slug,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"session {session_id} not found",
            )
    if row.status == "active":
        await _manager(request).cancel_session(session_id)
    async with sf() as s:
        # SET NULL on scan_history.session_id ; the linked scan_history
        # rows themselves are removed because they were just the session's
        # data and have no standalone value.
        await s.execute(
            delete(ScanHistoryRow).where(
                ScanHistoryRow.session_id == session_id,
            ),
        )
        sess = await s.get(SurveillanceSessionRow, session_id)
        if sess is not None:
            await s.delete(sess)
        await s.commit()
    logger.info(
        "wifi.surveillance.deleted",
        username=user.username, device=conn.slug, session_id=session_id,
    )


@router.get("/{session_id}/timeline")
async def get_timeline(
    session_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
    num_buckets: int = 80,
) -> dict[str, Any]:
    """Return classified per-BSSID timeline (see `SurveillanceManager.timeline_for`)."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(SurveillanceSessionRow).where(
                SurveillanceSessionRow.id == session_id,
                SurveillanceSessionRow.device_slug == conn.slug,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"session {session_id} not found",
            )
    return await _manager(request).timeline_for(
        session_id, num_buckets=max(20, min(num_buckets, 400)),
    )
