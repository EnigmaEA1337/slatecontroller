"""Storage for the controller's reachable URLs (LAN + Tailscale + preferred).

These URLs are what the Slate uses to call BACK to the controller — for
the button-hook on the Slate, the activation-commit endpoint, etc.

Two URLs are tracked because they apply in different contexts:
  - `tailscale_url` : reachable in mobility (e.g., http://100.x.x.x:8000
                      or http://<host>.taild2bce8.ts.net:8000). The Slate
                      hits this when it's NOT on the home LAN.
  - `lan_url`       : reachable on the home LAN (e.g.,
                      http://192.168.1.50:8000). Faster, lower latency.

The Slate-side hook script tries `preferred` first then falls back to the
other. Both are optional — if only one is set, that one is always used.

No encryption: these are URLs, not secrets. Stored as plain JSON in the
app_state singleton table (key="controller_urls").
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppStateRow

KEY = "controller_urls"


class ControllerUrlsStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> dict[str, Any]:
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            if row is None or not row.value:
                return {"tailscale_url": "", "lan_url": "", "preferred": "tailscale"}
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                return {"tailscale_url": "", "lan_url": "", "preferred": "tailscale"}
            # Normalize missing keys
            return {
                "tailscale_url": str(data.get("tailscale_url") or ""),
                "lan_url": str(data.get("lan_url") or ""),
                "preferred": str(data.get("preferred") or "tailscale"),
            }

    async def save(
        self,
        *,
        tailscale_url: str | None = None,
        lan_url: str | None = None,
        preferred: str | None = None,
    ) -> dict[str, Any]:
        current = await self.get()
        if tailscale_url is not None:
            current["tailscale_url"] = tailscale_url.strip()
        if lan_url is not None:
            current["lan_url"] = lan_url.strip()
        if preferred is not None:
            if preferred not in ("tailscale", "lan"):
                raise ValueError("preferred must be 'tailscale' or 'lan'")
            current["preferred"] = preferred

        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            payload = json.dumps(current)
            if row is None:
                s.add(AppStateRow(key=KEY, value=payload))
            else:
                row.value = payload
            await s.commit()
        return current
