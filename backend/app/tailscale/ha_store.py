"""Persistence for the Tailscale exit-node HA watchdog.

Two-part state, both in `app_secrets[key='tailscale_ha_config']`:

  config (user intent)             state (runtime, set by the watchdog)
  -------------------------       ---------------------------------------
  enabled              bool       last_action      "set"|"noop"|"down"|"error"|None
  candidates           list[str]  last_action_at   iso ts
  check_interval_seconds int      last_action_detail str
                                  last_target      str (what we set)
                                  last_switched_at iso ts (last successful switch)

`candidates` is an ORDERED list of preferred exit-node identifiers
(hostname OR Tailscale IP). The first one online wins.

Nothing here is sensitive — we store the whole thing in metadata_json
and leave encrypted_value empty to keep using the existing app_secrets
schema without adding columns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppSecretRow

SECRET_KEY = "tailscale_ha_config"

DEFAULT_CHECK_INTERVAL = 60  # seconds
MIN_CHECK_INTERVAL = 15
MAX_CHECK_INTERVAL = 600

# Failsafe modes when all configured exit-node candidates are offline.
#
# fail_open : drop --exit-node so the Slate falls back to its raw WAN
#             (preserves Internet at the cost of bypassing the tailnet exit).
#             Recommended default: avoids the "no internet because the
#             default route points at a dead tailscale0 peer" trap.
# keep      : keep the stale exit-node assignment (no killswitch). Use this
#             only when you'd rather have NO Internet than leak to the local
#             WAN — strict-privacy / data-exfil-prevention scenarios.
FAILSAFE_MODES = ("fail_open", "keep")
DEFAULT_FAILSAFE_MODE = "fail_open"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TailscaleHAStore:
    """Watchdog config + last-tick state, persisted across reboots."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> dict[str, Any]:
        """Return the full config + state. Defaults for unset entries."""
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
        meta = (row.metadata_json if row else None) or {}
        return {
            "enabled": bool(meta.get("enabled", False)),
            "candidates": list(meta.get("candidates") or []),
            "check_interval_seconds": int(
                meta.get("check_interval_seconds") or DEFAULT_CHECK_INTERVAL
            ),
            "failsafe_mode": meta.get("failsafe_mode") or DEFAULT_FAILSAFE_MODE,
            "last_action": meta.get("last_action"),
            "last_action_at": meta.get("last_action_at"),
            "last_action_detail": meta.get("last_action_detail"),
            "last_target": meta.get("last_target"),
            "last_switched_at": meta.get("last_switched_at"),
        }

    async def update_config(
        self,
        *,
        enabled: bool | None = None,
        candidates: list[str] | None = None,
        check_interval_seconds: int | None = None,
        failsafe_mode: str | None = None,
    ) -> dict[str, Any]:
        """Patch the user-intent half (writes a NEW row on first call)."""
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            meta: dict[str, Any] = dict(row.metadata_json or {}) if row else {}
            if enabled is not None:
                meta["enabled"] = bool(enabled)
            if candidates is not None:
                # Normalise: strip + drop empties, preserve order.
                meta["candidates"] = [c.strip() for c in candidates if c and c.strip()]
            if check_interval_seconds is not None:
                v = max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, int(check_interval_seconds)))
                meta["check_interval_seconds"] = v
            if failsafe_mode is not None:
                if failsafe_mode not in FAILSAFE_MODES:
                    raise ValueError(
                        f"failsafe_mode must be one of {FAILSAFE_MODES}, got {failsafe_mode!r}"
                    )
                meta["failsafe_mode"] = failsafe_mode
            if row is None:
                # encrypted_value is LargeBinary (bytes); we don't store a
                # secret here so use an empty bytes literal.
                s.add(AppSecretRow(key=SECRET_KEY, encrypted_value=b"", metadata_json=meta))
            else:
                row.metadata_json = meta
            await s.commit()
        return await self.get()

    async def record_tick(
        self,
        *,
        action: str,
        detail: str | None = None,
        target: str | None = None,
        switched: bool = False,
    ) -> None:
        """Update the runtime state half — called by the watchdog every tick."""
        async with self._sf() as s:
            row = await s.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SECRET_KEY)
            )
            if row is None:
                # No config yet → no state to record (watchdog wouldn't even run).
                return
            meta: dict[str, Any] = dict(row.metadata_json or {})
            now = _now_iso()
            meta["last_action"] = action
            meta["last_action_at"] = now
            meta["last_action_detail"] = detail
            if target is not None:
                meta["last_target"] = target
            if switched:
                meta["last_switched_at"] = now
            row.metadata_json = meta
            await s.commit()
