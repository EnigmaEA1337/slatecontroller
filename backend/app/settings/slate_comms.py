"""Communication settings — what the Slate Controller may DO ON the Slate.

Right now this controls one knob:
  show_screen_messages : when True, profile activations and other long
                          operations push a "MISE A JOUR" overlay onto the
                          Slate's front screen via direct framebuffer write.
                          When False, the operations run silently — the
                          panel keeps showing whatever GL.iNet renders.

Stored as plain JSON in `app_state[key="slate_comms"]`. Not secret.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppStateRow

KEY = "slate_comms"


class SlateCommsStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> dict[str, Any]:
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            if row is None or not row.value:
                return {"show_screen_messages": True}
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                return {"show_screen_messages": True}
            return {"show_screen_messages": bool(data.get("show_screen_messages", True))}

    async def save(self, *, show_screen_messages: bool | None = None) -> dict[str, Any]:
        current = await self.get()
        if show_screen_messages is not None:
            current["show_screen_messages"] = bool(show_screen_messages)
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            payload = json.dumps(current)
            if row is None:
                s.add(AppStateRow(key=KEY, value=payload))
            else:
                row.value = payload
            await s.commit()
        return current
