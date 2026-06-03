"""Per-device location history endpoints.

The active device exposes a timeline of where it has been. Each entry
is immutable ; "moving" the device means appending a new entry. The
most recent entry feeds scans as their default geolocation context.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.devices.locations import DeviceLocation, DeviceLocationStore
from app.devices.registry import DeviceConnections

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/devices/locations", tags=["devices", "location"])


class DeviceLocationView(BaseModel):
    id: int
    device_slug: str
    lat: float
    lon: float
    accuracy_m: float | None
    source: str
    label: str
    note: str
    created_at: str

    @classmethod
    def from_dataclass(cls, x: DeviceLocation) -> "DeviceLocationView":
        return cls(
            id=x.id, device_slug=x.device_slug,
            lat=x.lat, lon=x.lon, accuracy_m=x.accuracy_m,
            source=x.source, label=x.label, note=x.note,
            created_at=x.created_at.isoformat(),
        )


class DeviceLocationsResponse(BaseModel):
    device_slug: str
    current: DeviceLocationView | None
    history: list[DeviceLocationView]


class DeviceLocationCreate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    source: str = Field(default="manual", min_length=1, max_length=16)
    label: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=256)


def _store(request: Request) -> DeviceLocationStore:
    sf: async_sessionmaker = request.app.state.db_session_factory
    return DeviceLocationStore(sf)


@router.get("", response_model=DeviceLocationsResponse)
async def list_locations(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> DeviceLocationsResponse:
    """Return the active device's location history + current pointer."""
    store = _store(request)
    history = await store.list_for_device(conn.slug, limit=200)
    current = history[0] if history else None
    return DeviceLocationsResponse(
        device_slug=conn.slug,
        current=DeviceLocationView.from_dataclass(current) if current else None,
        history=[DeviceLocationView.from_dataclass(h) for h in history],
    )


@router.post("", response_model=DeviceLocationView, status_code=status.HTTP_201_CREATED)
async def add_location(
    body: DeviceLocationCreate,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> DeviceLocationView:
    """Append a new location entry. Becomes the new current."""
    store = _store(request)
    entry = await store.add(
        conn.slug,
        lat=body.lat, lon=body.lon, accuracy_m=body.accuracy_m,
        source=body.source, label=body.label, note=body.note,
    )
    logger.info(
        "device.location.added",
        username=user.username, device=conn.slug,
        lat=body.lat, lon=body.lon, source=body.source, label=body.label,
    )
    return DeviceLocationView.from_dataclass(entry)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete one history entry (the others are preserved)."""
    store = _store(request)
    ok = await store.delete(conn.slug, location_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"location {location_id} not found for device {conn.slug!r}",
        )
    logger.info(
        "device.location.deleted",
        username=user.username, device=conn.slug, location_id=location_id,
    )
