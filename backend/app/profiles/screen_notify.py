"""System-wide screen notification helper.

Wraps `display_message` with a check on the Settings → Communication
`show_screen_messages` toggle. Anywhere in the backend that wants to
flash a status on the Slate's panel calls this — the user controls
globally whether they fire or not.

Cheap helper: if the toggle is off, returns instantly without touching
the Slate. If on, pushes the fb takeover (which kills gl_screen → write
fb → restart). Best for brief notifications (2-3s); longer takeovers
fight the user's interaction with the panel.
"""

from __future__ import annotations

from typing import Literal

import structlog

from app.profiles.fb_takeover import FbTakeoverReport
from app.profiles.slate_message import display_message
from app.settings.slate_comms import SlateCommsStore
from app.slate.ssh import SlateSSH

logger = structlog.get_logger(__name__)

MessageKind = Literal["status", "action", "error", "ok"]


async def notify_screen(
    ssh: SlateSSH,
    comms_store: SlateCommsStore,
    *,
    title: str,
    subtitle: str = "depuis Slate Controller",
    target: str | None = None,
    kind: MessageKind = "status",
    duration_seconds: float = 2.5,
) -> FbTakeoverReport | None:
    """Toggle-gated screen notification. Returns None if disabled."""
    comms = await comms_store.get()
    if not comms.get("show_screen_messages", True):
        logger.info(
            "screen_notify.skipped",
            reason="show_screen_messages=false",
            title=title, kind=kind,
        )
        return None
    return await display_message(
        ssh, title=title, subtitle=subtitle, target=target,
        kind=kind, duration_seconds=duration_seconds, restart_after=True,
    )
