"""Async CRUD for the Wi-Fi SSID catalog."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import WifiSsidRow
from app.exceptions import SlateError
from app.vpn.crypto import decrypt, encrypt
from app.wifi.models import WifiSsidCreate, WifiSsidPublic, WifiSsidWrite


class WifiSsidError(SlateError):
    """Base error for the Wi-Fi store."""


class WifiSsidNotFoundError(WifiSsidError):
    pass


class WifiSsidDuplicateError(WifiSsidError):
    pass


def _to_public(row: WifiSsidRow) -> WifiSsidPublic:
    return WifiSsidPublic(
        slug=row.slug,
        ssid_name=row.ssid_name,
        bands=list(row.bands or []),  # type: ignore[arg-type]
        mlo=row.mlo,
        security=row.security,  # type: ignore[arg-type]
        client_isolation=row.client_isolation,
        hidden=row.hidden,
        notes=row.notes,
        has_password=bool(row.password_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class WifiSsidStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_all(self) -> list[WifiSsidPublic]:
        async with self._sf() as session:
            rows = (
                (await session.execute(select(WifiSsidRow).order_by(WifiSsidRow.slug)))
                .scalars()
                .all()
            )
            return [_to_public(r) for r in rows]

    async def get(self, slug: str) -> WifiSsidPublic:
        async with self._sf() as session:
            row = await session.scalar(
                select(WifiSsidRow).where(WifiSsidRow.slug == slug)
            )
            if row is None:
                raise WifiSsidNotFoundError(slug)
            return _to_public(row)

    async def get_password(self, slug: str) -> str:
        """Decrypt and return the SSID's PSK. Use sparingly (e.g. apply to Slate)."""
        async with self._sf() as session:
            row = await session.scalar(
                select(WifiSsidRow).where(WifiSsidRow.slug == slug)
            )
            if row is None:
                raise WifiSsidNotFoundError(slug)
            if not row.password_encrypted:
                return ""
            return decrypt(row.password_encrypted)

    async def create(self, body: WifiSsidCreate) -> WifiSsidPublic:
        encrypted = encrypt(body.password) if body.password else b""
        row = WifiSsidRow(
            slug=body.slug,
            ssid_name=body.ssid_name,
            bands=list(body.bands),
            mlo=body.mlo,
            security=body.security,
            password_encrypted=encrypted,
            client_isolation=body.client_isolation,
            hidden=body.hidden,
            notes=body.notes,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WifiSsidDuplicateError(body.slug) from exc
            await session.refresh(row)
            return _to_public(row)

    async def update(self, slug: str, body: WifiSsidWrite) -> WifiSsidPublic:
        async with self._sf() as session:
            row = await session.scalar(
                select(WifiSsidRow).where(WifiSsidRow.slug == slug)
            )
            if row is None:
                raise WifiSsidNotFoundError(slug)
            row.ssid_name = body.ssid_name
            row.bands = list(body.bands)
            row.mlo = body.mlo
            row.security = body.security
            row.client_isolation = body.client_isolation
            row.hidden = body.hidden
            row.notes = body.notes
            # password semantics: None = leave alone, "" = clear, anything else = set
            if body.password is not None:
                row.password_encrypted = encrypt(body.password) if body.password else b""
            await session.commit()
            await session.refresh(row)
            return _to_public(row)

    async def delete(self, slug: str) -> None:
        async with self._sf() as session:
            result = await session.execute(
                delete(WifiSsidRow).where(WifiSsidRow.slug == slug)
            )
            if result.rowcount == 0:
                raise WifiSsidNotFoundError(slug)
            await session.commit()

    async def seed_defaults(self, defaults: list[WifiSsidCreate]) -> int:
        """Insert defaults that don't already exist. Returns count inserted."""
        inserted = 0
        async with self._sf() as session:
            for default in defaults:
                exists = await session.scalar(
                    select(WifiSsidRow.id).where(WifiSsidRow.slug == default.slug)
                )
                if exists is not None:
                    continue
                encrypted = (
                    encrypt(default.password) if default.password else b""
                )
                session.add(
                    WifiSsidRow(
                        slug=default.slug,
                        ssid_name=default.ssid_name,
                        bands=list(default.bands),
                        mlo=default.mlo,
                        security=default.security,
                        password_encrypted=encrypted,
                        client_isolation=default.client_isolation,
                        hidden=default.hidden,
                        notes=default.notes,
                    )
                )
                inserted += 1
            if inserted:
                await session.commit()
        return inserted
