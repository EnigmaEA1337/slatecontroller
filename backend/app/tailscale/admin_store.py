"""Encrypted storage for the Tailscale admin PAT (Personal Access Token).

Lives in `app_secrets` (key='tailscale_admin_pat'), separate row from the
device auth_key. We never return the token in plain text — only metadata
(tailnet identifier captured at save-time, last-verified timestamp).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppSecretRow
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt

SECRET_KEY = "tailscale_admin_pat"


class TailscaleAdminStore:
    """Persist the PAT + the tailnet identifier we verified it against."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_metadata(self) -> dict[str, Any]:
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            if row is None:
                return {"configured": False, "tailnet": None, "last_verified_at": None}
            meta = row.metadata_json or {}
            return {
                "configured": bool(row.encrypted_value),
                "tailnet": meta.get("tailnet"),
                "last_verified_at": meta.get("last_verified_at"),
            }

    async def save(self, pat: str, tailnet: str) -> None:
        """Upsert. Caller should have validated `pat` against the API first."""
        async with self._sf() as s:
            existing = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            meta = {
                "tailnet": tailnet,
                "last_verified_at": datetime.now(UTC).isoformat(),
            }
            if existing:
                existing.encrypted_value = encrypt(pat)
                existing.metadata_json = meta
            else:
                s.add(
                    AppSecretRow(
                        key=SECRET_KEY,
                        encrypted_value=encrypt(pat),
                        metadata_json=meta,
                    )
                )
            await s.commit()

    async def get_pat(self) -> str | None:
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            if row is None or not row.encrypted_value:
                return None
        try:
            pat = decrypt(row.encrypted_value)
            return pat or None
        except VPNCryptoError:
            return None

    async def clear(self) -> None:
        async with self._sf() as s:
            await s.execute(delete(AppSecretRow).where(AppSecretRow.key == SECRET_KEY))
            await s.commit()
