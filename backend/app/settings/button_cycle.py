"""Reset-button profile cycle — store + helpers.

The Slate's reset button (under 3s press) cycles through a user-defined
ordered list of **steps**. Each step is either :
  - a **profile**  : `{kind: "profile", name: "mission"}` — the agent
    runs `slate-ctrl apply <name>` locally.
  - an **action**  : `{kind: "action", name: "update"}` — the agent
    invokes `/etc/slate-controller/scripts/cycle-action-<name>.sh` if
    that file exists. Unknown actions just log + advance without
    failing, so adding a step in the UI for an action we ship later is
    safe.

Why a typed shape rather than a list of strings : the user explicitly
asked for room to interleave special operations (e.g. "Update from
controller") between profile names. Mixing strings with sentinel
prefixes (`@update`) would collide with a future profile literally
named "update". Typed records are explicit.

The list is stored in `app_state[key='button_cycle']` and synced to the
Slate as `/etc/slate-controller/cycle.json` on every sync. The agent's
`cycle-profile.sh` reads that file at button-press time — 100% local
flow on the Slate, no controller round-trip required.

Empty list = no cycle (button-press is a logged no-op on the Slate).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppStateRow

KEY = "button_cycle"


class CycleStep(BaseModel):
    """One slot in the reset-button cycle."""

    kind: Literal["profile", "action"]
    name: str = Field(min_length=1, max_length=64)


class ButtonCycleStore:
    """Read/write the ordered list of cycle steps."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> list[CycleStep]:
        """Return the cycle steps. Empty list when unset."""
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            if row is None or not row.value:
                return []
            try:
                data = json.loads(row.value)
            except json.JSONDecodeError:
                return []
        raw_steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(raw_steps, list):
            return []
        out: list[CycleStep] = []
        for item in raw_steps:
            try:
                out.append(CycleStep.model_validate(item))
            except (ValueError, TypeError):
                # Drop malformed entries silently — the UI will re-write
                # the cleaned list on the next save.
                continue
        return out

    async def save(self, steps: list[CycleStep]) -> list[CycleStep]:
        """Replace the cycle list. Dedups consecutive identical steps to
        avoid surprises (two "mission" in a row would mean two presses
        to leave it)."""
        clean: list[CycleStep] = []
        for step in steps:
            if not isinstance(step, CycleStep):
                step = CycleStep.model_validate(step)
            step.name = step.name.strip()
            if not step.name:
                continue
            if clean and clean[-1].kind == step.kind and clean[-1].name == step.name:
                continue
            clean.append(step)
        payload = json.dumps(
            {"steps": [s.model_dump() for s in clean]},
        )
        async with self._sf() as s:
            row = await s.scalar(select(AppStateRow).where(AppStateRow.key == KEY))
            if row is None:
                s.add(AppStateRow(key=KEY, value=payload))
            else:
                row.value = payload
            await s.commit()
        return clean


def to_agent_payload(steps: list[CycleStep]) -> bytes:
    """Serialize the cycle list for the Slate's `/etc/slate-controller/cycle.json`."""
    body: dict[str, Any] = {
        "steps": [s.model_dump() for s in steps],
    }
    return (json.dumps(body) + "\n").encode("utf-8")


def remote_path() -> str:
    """Absolute path where `cycle.json` lives on the Slate."""
    return "/etc/slate-controller/cycle.json"
