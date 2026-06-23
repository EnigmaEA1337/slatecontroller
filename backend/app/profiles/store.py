"""Async DB store for contextual profiles + active-profile marker.

This replaces `ProfileManager` (YAML loader) as the source of truth for the
API surface. `ProfileManager` is still used to read shipped YAML templates
and seed the DB on first boot.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppStateRow, ProfileRow
from app.exceptions import SlateError
from app.models.profile import Profile

logger = structlog.get_logger(__name__)

ProfileSource = Literal["template", "user"]
ACTIVE_PROFILE_KEY = "active_profile"

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class ProfileStoreError(SlateError):
    """Base for profile-store errors."""


class ProfileNotFoundError(ProfileStoreError):
    """Asked for a profile name that isn't in the store."""


class ProfileDuplicateError(ProfileStoreError):
    """A profile with that name already exists."""


class ProfileImmutableError(ProfileStoreError):
    """Cannot delete or rename a shipped template (only user profiles)."""


class StoredProfile:
    """View object the API layer uses: profile + metadata (source, timestamps)."""

    def __init__(self, row: ProfileRow) -> None:
        self.profile: Profile = Profile.model_validate(row.payload)
        self.source: ProfileSource = row.source  # type: ignore[assignment]
        self.created_at = row.created_at
        self.updated_at = row.updated_at
        # NULL until the controller successfully pushes the resolved JSON
        # to /etc/slate-controller/profiles/<name>.json on the Slate. The
        # API derives ``out_of_sync = updated_at > last_synced_at`` to flag
        # profiles whose live edit hasn't been mirrored to the device yet
        # — those would be replayed stale by the button cycle / LCD.
        self.last_synced_at: datetime | None = row.last_synced_at

    @property
    def out_of_sync(self) -> bool:
        """True when this profile's local payload is newer than the JSON
        last pushed to the Slate (or was never pushed).

        Uses a 2-second tolerance because SQLAlchemy's ``onupdate`` hook on
        ``updated_at`` fires for any row change including a pure sync
        stamp — both ``last_synced_at`` and ``updated_at`` call
        ``datetime.now(UTC)`` sequentially in the same transaction, so
        ``updated_at`` lands a few microseconds (occasionally up to ~1s on
        a loaded SQLite) after ``last_synced_at``. Without tolerance every
        fresh sync would still look out of sync. 2 s is well above the
        intra-tx drift and well below any plausible user-edit-to-resync
        delay (a human takes seconds-to-minutes to even open the form
        again)."""
        if self.last_synced_at is None:
            return True
        return self.updated_at > self.last_synced_at + timedelta(seconds=2)


class ProfileStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def normalize_name(raw: str) -> str:
        slug = raw.strip().lower().replace(" ", "-")
        slug = re.sub(r"[^a-z0-9_-]", "", slug)
        if not _NAME_PATTERN.match(slug):
            raise ProfileStoreError(
                "name must be 1-63 chars of [a-z0-9_-], start with a letter or digit"
            )
        return slug

    async def list_all(self) -> list[StoredProfile]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(ProfileRow).order_by(ProfileRow.name)
                    )
                )
                .scalars()
                .all()
            )
            return [StoredProfile(r) for r in rows]

    async def get(self, name: str) -> StoredProfile:
        async with self._sf() as session:
            row = await session.scalar(
                select(ProfileRow).where(ProfileRow.name == name)
            )
            if row is None:
                raise ProfileNotFoundError(name)
            return StoredProfile(row)

    async def create(
        self, profile: Profile, *, source: ProfileSource = "user"
    ) -> StoredProfile:
        slug = self.normalize_name(profile.name)
        if slug != profile.name:
            # Force the slug onto the payload so listing stays consistent.
            profile = profile.model_copy(update={"name": slug})
        row = ProfileRow(
            name=slug,
            source=source,
            payload=profile.model_dump(mode="json"),
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ProfileDuplicateError(slug) from exc
            await session.refresh(row)
            return StoredProfile(row)

    async def update(self, name: str, profile: Profile) -> StoredProfile:
        """Edit an existing profile (any source). Name in URL is authoritative."""
        async with self._sf() as session:
            row = await session.scalar(
                select(ProfileRow).where(ProfileRow.name == name)
            )
            if row is None:
                raise ProfileNotFoundError(name)
            # Force payload name to match URL slug.
            updated = profile.model_copy(update={"name": name})
            row.payload = updated.model_dump(mode="json")
            await session.commit()
            await session.refresh(row)
            return StoredProfile(row)

    async def delete(self, name: str) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(ProfileRow).where(ProfileRow.name == name)
            )
            if row is None:
                raise ProfileNotFoundError(name)
            if row.source == "template":
                raise ProfileImmutableError(
                    f"profile {name!r} is a shipped template, cannot delete"
                )
            await session.execute(delete(ProfileRow).where(ProfileRow.name == name))
            # Clear active marker if it pointed here
            active = await session.scalar(
                select(AppStateRow).where(AppStateRow.key == ACTIVE_PROFILE_KEY)
            )
            if active is not None and active.value == name:
                active.value = ""
            await session.commit()

    async def mark_synced(self, name: str) -> None:
        """Stamp this profile as just-pushed to the Slate. Best-effort :
        if the row was deleted in the meantime, silently no-ops."""
        async with self._sf() as session:
            row = await session.scalar(
                select(ProfileRow).where(ProfileRow.name == name)
            )
            if row is None:
                return
            row.last_synced_at = datetime.now(UTC)
            await session.commit()

    async def duplicate(self, source_name: str, new_name: str) -> StoredProfile:
        original = await self.get(source_name)
        cloned = original.profile.model_copy(
            update={
                "name": self.normalize_name(new_name),
                "description": (original.profile.description or "")
                + f" (copie de {source_name})",
            }
        )
        return await self.create(cloned, source="user")

    # ---------------------------- active marker ---------------------------- #

    async def get_active_name(self) -> str | None:
        async with self._sf() as session:
            row = await session.scalar(
                select(AppStateRow).where(AppStateRow.key == ACTIVE_PROFILE_KEY)
            )
            if row is None or not row.value:
                return None
            return row.value

    async def set_active(self, name: str) -> None:
        # Existence check + write must share one transaction (nightly
        # audit 2026-06-23 low) — splitting them across two sessions
        # opened a race where DELETE /profiles/{name} could land between
        # the get() and the AppState write, leaving the marker pointing
        # at a deleted profile and crashing the next /active read.
        async with self._sf() as session:
            row = await session.scalar(
                select(ProfileRow).where(ProfileRow.name == name)
            )
            if row is None:
                raise ProfileNotFoundError(name)
            active_row = await session.scalar(
                select(AppStateRow).where(AppStateRow.key == ACTIVE_PROFILE_KEY)
            )
            if active_row is None:
                session.add(AppStateRow(key=ACTIVE_PROFILE_KEY, value=name))
            else:
                active_row.value = name
            await session.commit()

    # ---------------------------- seed helpers ---------------------------- #

    async def is_empty(self) -> bool:
        async with self._sf() as session:
            count = await session.scalar(select(ProfileRow.id).limit(1))
            return count is None

    async def seed_from(self, profiles: list[Profile]) -> int:
        """Insert each profile as a template. Skips names already present."""
        inserted = 0
        async with self._sf() as session:
            for profile in profiles:
                exists = await session.scalar(
                    select(ProfileRow.id).where(ProfileRow.name == profile.name)
                )
                if exists is not None:
                    continue
                session.add(
                    ProfileRow(
                        name=profile.name,
                        source="template",
                        payload=profile.model_dump(mode="json"),
                    )
                )
                inserted += 1
            if inserted:
                await session.commit()
                logger.info("profiles.seed.ok", count=inserted)
        return inserted
