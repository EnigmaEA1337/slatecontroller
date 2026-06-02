"""Storage for the tailnet admin IP whitelist.

The whitelist drives the ``Profile.tailscale.admin_only`` flag : when a
profile activates with ``admin_only=true``, only peers whose tailnet IP
appears in this list can reach the Slate's admin surface (SSH 22, LuCI
80/443, AdGuard 3000, slate-ctrl 8000). Every other tailnet peer can
still use the Slate as a router / subnet-route gateway / exit-node — the
restriction is only on the *admin plane*.

Why a global setting (not per-profile / per-device) :
  - The admins of a tailnet are a property of the *operator*, not of a
    specific profile or Slate. Re-typing the list on every profile would
    be redundant and a footgun (drift between profiles → admin lockout).
  - One Slate today, more later — the same admin set applies to all.

Storage : `AppStateRow[key="tailnet_admin"]`, JSON value with shape
::

    { "admin_ips": ["100.64.0.5", "100.64.0.12"] }

Validation : light — each entry must look like a printable host token
(IPv4, IPv6, or short MagicDNS name). We don't enforce the 100.64/10
range : a) Tailscale uses 100.64/10 for IPv4 but ALSO has IPv6 ULA-style
addresses ; b) MagicDNS hostnames are valid too if you prefer naming
peers. The firewall handler on the Slate is the authoritative validator.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppStateRow

KEY = "tailnet_admin"

# Lenient regex : IPv4-ish OR IPv6-ish OR a short DNS label. The Slate
# handler does the real check (the firewall command rejects malformed
# inputs anyway).
_TOKEN_RE = re.compile(
    r"^[0-9a-fA-F:.-]+$"           # IPv4 / IPv6 numerics
    r"|^[a-zA-Z][a-zA-Z0-9.-]{0,62}$"  # MagicDNS-style host name
)


def _normalize(values: list[str] | None) -> list[str]:
    """Strip empties, drop dupes, preserve insertion order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        v = (raw or "").strip()
        if not v or v in seen:
            continue
        if not _TOKEN_RE.match(v):
            raise ValueError(
                f"{v!r} doesn't look like an IP or MagicDNS host",
            )
        seen.add(v)
        out.append(v)
    return out


class TailnetAdminStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> dict[str, Any]:
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            if row is None or not row.value:
                return {"admin_ips": []}
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                return {"admin_ips": []}
            return {"admin_ips": list(data.get("admin_ips") or [])}

    async def save(self, admin_ips: list[str]) -> dict[str, Any]:
        cleaned = _normalize(admin_ips)
        payload = {"admin_ips": cleaned}
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            serialized = json.dumps(payload)
            if row is None:
                s.add(AppStateRow(key=KEY, value=serialized))
            else:
                row.value = serialized
            await s.commit()
        return payload
