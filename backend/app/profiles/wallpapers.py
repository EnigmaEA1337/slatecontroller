"""Profile wallpaper storage — CRUD on the profile_wallpapers BLOB table.

Two kinds per profile, both optional and independent:
  - 'home' → /etc/gl_screen/wallpaper_home.png  (nav screen)
  - 'lock' → /etc/gl_screen/wallpaper_wake_display.png  (lock/wake screen)

Each blob carries a fit_mode hint (contain / cover / stretch) consumed by
the screen_applier when resizing to the screen's native 320×240 canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProfileWallpaperRow

ALLOWED_MIME = ("image/png", "image/jpeg", "image/webp")
MAX_BYTES = 5 * 1024 * 1024  # 5 MiB

KINDS = ("home", "lock")
WallpaperKind = Literal["home", "lock"]

FIT_MODES = ("contain", "cover", "stretch")
FitMode = Literal["contain", "cover", "stretch"]


class WallpaperError(Exception):
    """Validation or storage error for a wallpaper operation."""


@dataclass
class WallpaperRecord:
    profile_name: str
    kind: str
    fit_mode: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime


@dataclass
class WallpaperBlob(WallpaperRecord):
    content: bytes


class WallpaperStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_meta(
        self, profile_name: str, kind: str = "home"
    ) -> WallpaperRecord | None:
        async with self._sf() as s:
            row = await s.scalar(
                select(ProfileWallpaperRow).where(
                    ProfileWallpaperRow.profile_name == profile_name,
                    ProfileWallpaperRow.kind == kind,
                )
            )
            if row is None:
                return None
            return _row_to_meta(row)

    async def get_blob(
        self, profile_name: str, kind: str = "home"
    ) -> WallpaperBlob | None:
        async with self._sf() as s:
            row = await s.scalar(
                select(ProfileWallpaperRow).where(
                    ProfileWallpaperRow.profile_name == profile_name,
                    ProfileWallpaperRow.kind == kind,
                )
            )
            if row is None:
                return None
            return WallpaperBlob(
                profile_name=row.profile_name,
                kind=row.kind,
                fit_mode=row.fit_mode,
                mime_type=row.mime_type,
                size_bytes=row.size_bytes,
                uploaded_at=row.uploaded_at,
                content=bytes(row.content),
            )

    async def list_existing(self) -> dict[tuple[str, str], WallpaperRecord]:
        """Map (profile_name, kind) → metadata for every existing wallpaper.

        The profile-list endpoint uses this in a single query to set the
        has_wallpaper_* flags per profile envelope without N+1.
        """
        async with self._sf() as s:
            rows = (await s.scalars(select(ProfileWallpaperRow))).all()
            return {(r.profile_name, r.kind): _row_to_meta(r) for r in rows}

    async def upsert(
        self,
        profile_name: str,
        content: bytes,
        mime_type: str,
        *,
        kind: str = "home",
        fit_mode: str = "contain",
    ) -> WallpaperRecord:
        if kind not in KINDS:
            raise WallpaperError(f"kind must be one of {KINDS}, got {kind!r}")
        if fit_mode not in FIT_MODES:
            raise WallpaperError(f"fit_mode must be one of {FIT_MODES}, got {fit_mode!r}")
        if mime_type not in ALLOWED_MIME:
            raise WallpaperError(
                f"mime_type {mime_type!r} not allowed; expected one of {ALLOWED_MIME}"
            )
        size = len(content)
        if size == 0:
            raise WallpaperError("empty content")
        if size > MAX_BYTES:
            raise WallpaperError(f"file too large: {size} bytes > limit {MAX_BYTES}")
        async with self._sf() as s:
            existing = await s.scalar(
                select(ProfileWallpaperRow).where(
                    ProfileWallpaperRow.profile_name == profile_name,
                    ProfileWallpaperRow.kind == kind,
                )
            )
            if existing is not None:
                existing.mime_type = mime_type
                existing.content = content
                existing.size_bytes = size
                existing.fit_mode = fit_mode
                existing.uploaded_at = datetime.utcnow()
            else:
                s.add(
                    ProfileWallpaperRow(
                        profile_name=profile_name,
                        kind=kind,
                        fit_mode=fit_mode,
                        mime_type=mime_type,
                        content=content,
                        size_bytes=size,
                    )
                )
            await s.commit()
        meta = await self.get_meta(profile_name, kind)
        assert meta is not None  # noqa: S101 — just committed
        return meta

    async def delete(self, profile_name: str, kind: str = "home") -> bool:
        async with self._sf() as s:
            r = await s.execute(
                delete(ProfileWallpaperRow).where(
                    ProfileWallpaperRow.profile_name == profile_name,
                    ProfileWallpaperRow.kind == kind,
                )
            )
            await s.commit()
            return (r.rowcount or 0) > 0

    async def delete_all(self, profile_name: str) -> int:
        """Cascade on profile delete — wipe both kinds in one query."""
        async with self._sf() as s:
            r = await s.execute(
                delete(ProfileWallpaperRow).where(
                    ProfileWallpaperRow.profile_name == profile_name
                )
            )
            await s.commit()
            return r.rowcount or 0

    async def rename(self, old_name: str, new_name: str) -> None:
        async with self._sf() as s:
            await s.execute(
                update(ProfileWallpaperRow)
                .where(ProfileWallpaperRow.profile_name == old_name)
                .values(profile_name=new_name)
            )
            await s.commit()


def _row_to_meta(row: ProfileWallpaperRow) -> WallpaperRecord:
    return WallpaperRecord(
        profile_name=row.profile_name,
        kind=row.kind,
        fit_mode=row.fit_mode,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        uploaded_at=row.uploaded_at,
    )
