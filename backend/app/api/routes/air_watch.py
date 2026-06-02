"""Air Watch — RF threat detections surface for AUDIT.

Persisted threat events written by the scanner are listed here, with
basic dismiss/restore controls so the operator can curate false
positives. The current implementation is read-only-ish (list + dismiss)
to ship the surface fast ; future iterations will add :
  - continuous monitor-mode capture (separate slot, kismet-style)
  - per-event signal strength sparkline
  - Slack / Pushover alerts on alert-level events
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.db.models import ThreatEventRow
from app.devices.registry import DeviceConnections

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/security/air-watch", tags=["security", "air-watch"])


class ThreatEventDb(BaseModel):
    id: int
    kind: str
    level: str
    bssid: str
    ssid: str
    channel: int
    rssi_dbm: int
    message: str
    first_seen_at: datetime
    last_seen_at: datetime
    dismissed: bool


class AirWatchSummary(BaseModel):
    total: int
    active: int
    dismissed: int
    by_level: dict[str, int]
    by_kind: dict[str, int]
    events: list[ThreatEventDb]


@router.get("", response_model=AirWatchSummary)
async def list_threats(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> AirWatchSummary:
    """Return the threat timeline for the active device."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        rows = (await s.scalars(
            select(ThreatEventRow)
            .where(ThreatEventRow.device_slug == conn.slug)
            .order_by(ThreatEventRow.last_seen_at.desc()),
        )).all()
    events = [
        ThreatEventDb(
            id=r.id, kind=r.kind, level=r.level, bssid=r.bssid, ssid=r.ssid,
            channel=r.channel, rssi_dbm=r.rssi_dbm, message=r.message,
            first_seen_at=r.first_seen_at, last_seen_at=r.last_seen_at,
            dismissed=r.dismissed,
        )
        for r in rows
    ]
    by_level: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    active = 0
    dismissed = 0
    for e in events:
        if e.dismissed:
            dismissed += 1
        else:
            active += 1
            by_level[e.level] = by_level.get(e.level, 0) + 1
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    return AirWatchSummary(
        total=len(events), active=active, dismissed=dismissed,
        by_level=by_level, by_kind=by_kind, events=events,
    )


@router.post("/{event_id}/dismiss", response_model=ThreatEventDb)
async def dismiss_threat(
    event_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> ThreatEventDb:
    """Mark a threat as dismissed (false positive / accepted risk)."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(ThreatEventRow).where(
                ThreatEventRow.id == event_id,
                ThreatEventRow.device_slug == conn.slug,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"threat event {event_id} not found",
            )
        row.dismissed = True
        row.dismissed_at = datetime.now(UTC)
        await s.commit()
        await s.refresh(row)
    logger.info(
        "air_watch.dismissed",
        username=user.username, event_id=event_id, kind=row.kind, bssid=row.bssid,
    )
    return ThreatEventDb(
        id=row.id, kind=row.kind, level=row.level, bssid=row.bssid, ssid=row.ssid,
        channel=row.channel, rssi_dbm=row.rssi_dbm, message=row.message,
        first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at,
        dismissed=row.dismissed,
    )


@router.post("/{event_id}/restore", response_model=ThreatEventDb)
async def restore_threat(
    event_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> ThreatEventDb:
    """Un-dismiss a threat (reactivate it for the operator's attention)."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(ThreatEventRow).where(
                ThreatEventRow.id == event_id,
                ThreatEventRow.device_slug == conn.slug,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"threat event {event_id} not found",
            )
        row.dismissed = False
        row.dismissed_at = None
        await s.commit()
        await s.refresh(row)
    logger.info(
        "air_watch.restored",
        username=user.username, event_id=event_id, kind=row.kind, bssid=row.bssid,
    )
    return ThreatEventDb(
        id=row.id, kind=row.kind, level=row.level, bssid=row.bssid, ssid=row.ssid,
        channel=row.channel, rssi_dbm=row.rssi_dbm, message=row.message,
        first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at,
        dismissed=row.dismissed,
    )
