"""Storage for global Tor daemon settings + bridges.

Tables : ``tor_settings`` (singleton, id=1) + ``tor_bridges`` (1 row per
bridge line). Per-network Tor toggles live on :class:`NetworkRow` —
*not* here.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import TorBridgeRow, TorSettingsRow
from app.exceptions import SlateError
from app.tor.models import TorBridge, TorBridgeWrite, TorSettings, TorSettingsWrite


class TorError(SlateError):
    """Base class for Tor store / API errors."""


class TorBridgeNotFoundError(TorError):
    """Raised when a bridge id can't be found."""


def _settings_to_public(row: TorSettingsRow) -> TorSettings:
    return TorSettings(
        daemon_enabled=row.daemon_enabled,
        use_bridges=row.use_bridges,
        exit_country_code=row.exit_country_code,
        updated_at=row.updated_at,
    )


def _bridge_to_public(row: TorBridgeRow) -> TorBridge:
    return TorBridge(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        bridge_line=row.bridge_line,
        note=row.note,
        enabled=row.enabled,
        created_at=row.created_at,
    )


class TorSettingsStore:
    """Singleton store : one row with id=1 holding the global toggles."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _get_or_create(self, s: AsyncSession) -> TorSettingsRow:
        row = await s.get(TorSettingsRow, 1)
        if row is None:
            row = TorSettingsRow(id=1, daemon_enabled=False, use_bridges=False)
            s.add(row)
            await s.flush()
        return row

    async def get(self) -> TorSettings:
        async with self._sf() as s:
            row = await self._get_or_create(s)
            await s.commit()
            return _settings_to_public(row)

    async def save(self, body: TorSettingsWrite) -> TorSettings:
        async with self._sf() as s:
            row = await self._get_or_create(s)
            row.daemon_enabled = body.daemon_enabled
            row.use_bridges = body.use_bridges
            row.exit_country_code = body.exit_country_code.lower().strip()
            await s.commit()
            await s.refresh(row)
            return _settings_to_public(row)


class TorBridgeStore:
    """CRUD over the configured Tor bridges."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_all(self) -> list[TorBridge]:
        async with self._sf() as s:
            rows = (
                await s.execute(select(TorBridgeRow).order_by(TorBridgeRow.id))
            ).scalars().all()
            return [_bridge_to_public(r) for r in rows]

    async def list_enabled_lines(self) -> list[str]:
        """Just the raw ``bridge_line`` values that are currently enabled —
        what the agent needs to emit ``Bridge ...`` directives in torrc.
        Stable order = creation order.
        """
        async with self._sf() as s:
            rows = (
                await s.execute(
                    select(TorBridgeRow)
                    .where(TorBridgeRow.enabled.is_(True))
                    .order_by(TorBridgeRow.id)
                )
            ).scalars().all()
            return [r.bridge_line for r in rows]

    async def create(self, body: TorBridgeWrite) -> TorBridge:
        async with self._sf() as s:
            row = TorBridgeRow(
                kind=body.kind,
                bridge_line=body.bridge_line.strip(),
                note=body.note,
                enabled=body.enabled,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _bridge_to_public(row)

    async def update(self, bridge_id: int, body: TorBridgeWrite) -> TorBridge:
        async with self._sf() as s:
            row = await s.get(TorBridgeRow, bridge_id)
            if row is None:
                raise TorBridgeNotFoundError(
                    f"Tor bridge id={bridge_id} not found",
                )
            row.kind = body.kind
            row.bridge_line = body.bridge_line.strip()
            row.note = body.note
            row.enabled = body.enabled
            await s.commit()
            await s.refresh(row)
            return _bridge_to_public(row)

    async def delete(self, bridge_id: int) -> None:
        async with self._sf() as s:
            row = await s.get(TorBridgeRow, bridge_id)
            if row is None:
                raise TorBridgeNotFoundError(
                    f"Tor bridge id={bridge_id} not found",
                )
            await s.execute(
                delete(TorBridgeRow).where(TorBridgeRow.id == bridge_id),
            )
            await s.commit()
