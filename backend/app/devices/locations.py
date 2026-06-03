"""Per-device location history store.

A device's location is a timeline of (lat, lon, source, label) entries.
The most recent entry is the device's "current" location and feeds
scans, threat maps, and any feature that needs to anchor the device
in the real world.

Each entry stays immutable once created — that's the whole point of
keeping it as a history. To "move" the device, the operator just adds
a new entry. To correct a typo, the operator deletes the wrong entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DeviceLocationRow


@dataclass(frozen=True)
class DeviceLocation:
    id: int
    device_slug: str
    lat: float
    lon: float
    accuracy_m: float | None
    source: str
    label: str
    note: str
    created_at: datetime


class DeviceLocationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _row_to_dataclass(row: DeviceLocationRow) -> DeviceLocation:
        return DeviceLocation(
            id=row.id, device_slug=row.device_slug,
            lat=row.lat, lon=row.lon, accuracy_m=row.accuracy_m,
            source=row.source, label=row.label, note=row.note,
            created_at=row.created_at,
        )

    async def list_for_device(
        self, device_slug: str, limit: int = 100,
    ) -> list[DeviceLocation]:
        """Return the location history, newest first."""
        async with self._sf() as s:
            rows = (await s.scalars(
                select(DeviceLocationRow)
                .where(DeviceLocationRow.device_slug == device_slug)
                .order_by(desc(DeviceLocationRow.created_at))
                .limit(limit),
            )).all()
        return [self._row_to_dataclass(r) for r in rows]

    async def current(self, device_slug: str) -> DeviceLocation | None:
        """Return the device's current location = most recent entry."""
        async with self._sf() as s:
            row = await s.scalar(
                select(DeviceLocationRow)
                .where(DeviceLocationRow.device_slug == device_slug)
                .order_by(desc(DeviceLocationRow.created_at))
                .limit(1),
            )
        if row is None:
            return None
        return self._row_to_dataclass(row)

    async def add(
        self,
        device_slug: str,
        *,
        lat: float,
        lon: float,
        accuracy_m: float | None = None,
        source: str = "manual",
        label: str = "",
        note: str = "",
    ) -> DeviceLocation:
        """Append a new entry. The previous "current" entry is preserved."""
        async with self._sf() as s:
            row = DeviceLocationRow(
                device_slug=device_slug,
                lat=lat, lon=lon, accuracy_m=accuracy_m,
                source=source, label=label, note=note,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return self._row_to_dataclass(row)

    async def delete(self, device_slug: str, location_id: int) -> bool:
        """Delete one entry. Returns True if something was removed."""
        async with self._sf() as s:
            row = await s.scalar(
                select(DeviceLocationRow).where(
                    DeviceLocationRow.id == location_id,
                    DeviceLocationRow.device_slug == device_slug,
                ),
            )
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True
