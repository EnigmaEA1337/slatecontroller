"""Event handlers wired into the WebhookDispatcher at boot.

Each handler is async and signs the same ``(slug, payload, sent_at) ->
None`` contract. Failures bubble up — the route returns 500, the Slate
push helper retries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.scheduler.screen_lock_watcher import ScreenLockWatcher
from app.slate.screen_lock import TouchscreenLockoutTelemetry

logger = structlog.get_logger(__name__)


def build_touchscreen_status_handler(watcher: ScreenLockWatcher):
    """Return the handler closure that bridges incoming push events to
    the existing ``apply_telemetry`` path on the watcher — same diff
    logic as the poll fallback, just driven by push."""

    async def handle(
        slug: str, payload: dict[str, Any], sent_at: datetime | None,
    ) -> None:
        errors = payload.get("continuous_errors", 0)
        exceed_count = payload.get("exceed_count", 0)
        try:
            errors = int(errors)
            exceed_count = int(exceed_count)
        except (TypeError, ValueError):
            logger.warning(
                "webhook.touchscreen_status.bad_payload",
                slug=slug, payload=payload,
            )
            return
        tel = TouchscreenLockoutTelemetry(
            continuous_errors=max(0, errors),
            exceed_count=max(0, exceed_count),
            exceed_limit=exceed_count > 0,
        )
        # apply_telemetry now only logs when state actually changes ;
        # the on-device watcher heartbeats every 30s so most pushes
        # carry the same data as last time — silent is the right path.
        await watcher.apply_telemetry(slug, tel, source="push")

    return handle
