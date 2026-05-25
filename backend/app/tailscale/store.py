"""Persist Tailscale config (encrypted auth key + last applied config)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppSecretRow
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt

SECRET_KEY = "tailscale_config"


class TailscaleStore:
    """One row in `app_secrets` (key='tailscale_config') holds:

      - encrypted_value: encrypted auth key (plaintext str → fernet)
      - metadata_json: last-applied config dict (no secrets)

    We never log or return the auth key in plain text — only a "configured"
    boolean.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_metadata(self) -> dict[str, Any]:
        """Return the last-applied config + has-auth-key flag."""
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            if row is None:
                return {"has_auth_key": False, "config": None}
            meta = row.metadata_json or {}
            return {
                "has_auth_key": bool(row.encrypted_value),
                "config": meta.get("last_applied_config"),
                "last_applied_at": meta.get("last_applied_at"),
            }

    async def save(
        self, auth_key: str | None, last_applied_config: dict[str, Any]
    ) -> None:
        """Upsert. If auth_key is None, keep the previously stored one."""
        async with self._sf() as s:
            existing = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            if existing:
                if auth_key is not None:
                    existing.encrypted_value = encrypt(auth_key)
                meta = existing.metadata_json or {}
                meta["last_applied_config"] = last_applied_config
                from datetime import UTC, datetime as _dt
                meta["last_applied_at"] = _dt.now(UTC).isoformat()
                existing.metadata_json = meta
            else:
                from datetime import UTC, datetime as _dt
                s.add(
                    AppSecretRow(
                        key=SECRET_KEY,
                        encrypted_value=encrypt(auth_key or ""),
                        metadata_json={
                            "last_applied_config": last_applied_config,
                            "last_applied_at": _dt.now(UTC).isoformat(),
                        },
                    )
                )
            await s.commit()

    async def get_auth_key(self) -> str | None:
        """Decrypt and return the stored auth key. None if not set."""
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            if row is None or not row.encrypted_value:
                return None
        try:
            key = decrypt(row.encrypted_value)
            return key or None
        except VPNCryptoError:
            return None

    async def clear(self) -> None:
        """Wipe auth key + config. Called from /logout endpoint."""
        from sqlalchemy import delete
        async with self._sf() as s:
            await s.execute(delete(AppSecretRow).where(AppSecretRow.key == SECRET_KEY))
            await s.commit()
