"""Touchscreen lockout watcher — bridge gl_screen → anti-theft.

Polls ``/etc/gl_screen/status`` over SSH every ``poll_interval_s``
seconds per device. Compares against the in-memory snapshot to detect :

  - New failures (``continuous_errors`` increased) → forwards each
    delta to :class:`AntiTheftService.on_pin_failure`, so the cumulative
    counter ticks and the soft_wipe / alert action fires when the
    operator-configured threshold is hit by *touchscreen* attempts.

  - Lockout transition (``exceed_limit`` 0→1) → recorded so the UI
    can show "Touchscreen verrouillé" in the banner.

  - Successful unlock vs lockout-timeout : counter dropping to 0 while
    ``exceed_limit`` was 0 = real success → forward as
    :meth:`AntiTheftService.on_pin_success` to reset the cumulative
    counter. Drop to 0 while ``exceed_limit`` was 1 = window expired,
    we don't reset anything (the failures still count toward the
    cumulative threshold).

The snapshot lives in memory : we don't persist it because the next
poll re-establishes ground truth, and missing one transition across
a restart is a non-event (worst case : a couple of failures don't
count, the next ones do).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.devices.registry import DeviceConnectionsRegistry
from app.security.anti_theft import AntiTheftService
from app.slate.screen_lock import (
    ScreenLockError,
    TouchscreenLockoutTelemetry,
    read_touchscreen_lockout,
)

logger = structlog.get_logger(__name__)


_JOB_ID = "touchscreen_lockout_watcher"


@dataclass
class TouchscreenState:
    """Public-facing latest known state for a device's touchscreen."""

    continuous_errors: int = 0
    exceed_count: int = 0
    exceed_limit: bool = False
    last_polled_at: datetime | None = None
    last_error: str = ""


class ScreenLockWatcher:
    """Owns the polling job that bridges gl_screen → anti-theft AND the
    in-memory snapshot consumed by both poll + push paths.

    The webhook push handler (``app/webhooks/handlers.py``) also calls
    :meth:`apply_telemetry` so the snapshot stays the single source of
    truth regardless of how the data arrived. The poll path is the
    fallback when the push is unreachable (controller offline at the
    moment of the event, Slate WAN drop, etc.).
    """

    def __init__(
        self,
        *,
        scheduler: AsyncIOScheduler,
        device_registry: DeviceConnectionsRegistry,
        anti_theft: AntiTheftService,
        poll_interval_s: int = 60,
    ) -> None:
        self._scheduler = scheduler
        self._dev = device_registry
        self._anti = anti_theft
        self._poll_s = poll_interval_s
        # Per-device : (last_telemetry, public_state).
        self._snapshots: dict[
            str, tuple[TouchscreenLockoutTelemetry, TouchscreenState]
        ] = {}

    def register(self) -> None:
        self._scheduler.add_job(
            self._poll_all,
            IntervalTrigger(seconds=self._poll_s),
            id=_JOB_ID,
            name="Touchscreen lockout watcher",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=self._poll_s,
        )
        logger.info(
            "screen_lock_watcher.registered", poll_s=self._poll_s,
        )

    def get_state(self, slug: str) -> TouchscreenState:
        """UI-facing latest snapshot. Defaults if never polled."""
        snap = self._snapshots.get(slug)
        return snap[1] if snap else TouchscreenState()

    async def _poll_all(self) -> None:
        """Iterate every device in the registry's cache. Devices that
        haven't been built yet are skipped — they'll be picked up on the
        next poll once the routes warm the registry."""
        # The registry's cache attribute is private but stable.
        slugs = list(getattr(self._dev, "_cache", {}).keys())
        if not slugs:
            return
        for slug in slugs:
            try:
                await self._poll_one(slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "screen_lock_watcher.poll_failed",
                    slug=slug, error=str(exc),
                )

    async def _poll_one(self, slug: str) -> None:
        try:
            conn = await self._dev.for_slug(slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "screen_lock_watcher.no_device",
                slug=slug, error=str(exc),
            )
            return
        try:
            tel = await read_touchscreen_lockout(conn.ssh)
        except ScreenLockError as exc:
            prev = self._snapshots.get(slug)
            now = datetime.now(UTC).replace(tzinfo=None)
            public = TouchscreenState(
                continuous_errors=prev[1].continuous_errors if prev else 0,
                exceed_count=prev[1].exceed_count if prev else 0,
                exceed_limit=prev[1].exceed_limit if prev else False,
                last_polled_at=now,
                last_error=str(exc)[:128],
            )
            self._snapshots[slug] = (
                prev[0] if prev else TouchscreenLockoutTelemetry(0, 0, False),
                public,
            )
            return

        await self.apply_telemetry(slug, tel, source="poll")

    async def apply_telemetry(
        self,
        slug: str,
        tel: TouchscreenLockoutTelemetry,
        *,
        source: str = "poll",
    ) -> None:
        """Update the snapshot with a fresh telemetry sample AND fire
        anti-theft hooks for any new transition. Called by both the poll
        loop and the webhook push handler."""
        now = datetime.now(UTC).replace(tzinfo=None)
        prev = self._snapshots.get(slug)
        prev_tel = (
            prev[0] if prev else TouchscreenLockoutTelemetry(0, 0, False)
        )
        changed = (
            prev_tel.continuous_errors != tel.continuous_errors
            or prev_tel.exceed_count != tel.exceed_count
        )
        await self._diff_and_dispatch(slug, prev_tel, tel)
        self._snapshots[slug] = (
            tel,
            TouchscreenState(
                continuous_errors=tel.continuous_errors,
                exceed_count=tel.exceed_count,
                exceed_limit=tel.exceed_limit,
                last_polled_at=now,
                last_error="",
            ),
        )
        # Only log when there's an actual delta — heartbeats and the
        # 5-min safety polls would otherwise flood the log with noise.
        if changed:
            logger.info(
                "screen_lock_watcher.snapshot_applied",
                slug=slug, source=source,
                errors=tel.continuous_errors, exceed=tel.exceed_count,
            )

    async def _diff_and_dispatch(
        self,
        slug: str,
        prev: TouchscreenLockoutTelemetry,
        cur: TouchscreenLockoutTelemetry,
    ) -> None:
        # New failures are detected from TWO sources, because gl_screen
        # shuffles the count between two fields at the lockout boundary :
        #
        #   1. ``continuous_errors`` increases between polls = pure new
        #      failures within the same streak.
        #   2. ``exceed_count`` increases between polls = lockout(s)
        #      newly triggered. Each one represents a streak that
        #      reached the gl_screen threshold (default 5) → that many
        #      failures happened that we can't see individually because
        #      gl_screen wiped ``continuous_errors`` on lockout.
        #
        # Counting both keeps the cumulative counter honest even when
        # we miss the transient state between polls.
        errors_delta = max(0, cur.continuous_errors - prev.continuous_errors)
        exceed_delta = max(0, cur.exceed_count - prev.exceed_count)
        # On a fresh lockout the previous continuous_errors are also
        # "lost" into the lockout — they were real failures we already
        # counted, so we don't double-count them : only the NEW exceed
        # delta is added.
        new_failures = errors_delta + exceed_delta

        if new_failures > 0:
            logger.info(
                "screen_lock_watcher.failures_detected",
                slug=slug, delta=new_failures,
                errors_delta=errors_delta, exceed_delta=exceed_delta,
                prev_err=prev.continuous_errors, cur_err=cur.continuous_errors,
                prev_exc=prev.exceed_count, cur_exc=cur.exceed_count,
            )
            for _ in range(new_failures):
                try:
                    await self._anti.on_pin_failure(slug)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "screen_lock_watcher.anti_theft_failure_hook_failed",
                        slug=slug, error=str(exc),
                    )

        # Success detection : only fires when EVERYTHING goes back to
        # zero AND we were NOT just transitioning into lockout. The
        # subtle case the previous code got wrong : gl_screen sometimes
        # zeroes ``continuous_errors`` while LOCKING (count moves to
        # exceed_count). So requiring cur.exceed_count == 0 too prevents
        # the false-positive that wiped our counter on lockout entry.
        prev_had_state = prev.continuous_errors > 0 or prev.exceed_count > 0
        cur_clean = cur.continuous_errors == 0 and cur.exceed_count == 0
        if prev_had_state and cur_clean:
            logger.info(
                "screen_lock_watcher.success_detected", slug=slug,
            )
            try:
                await self._anti.on_pin_success(slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "screen_lock_watcher.anti_theft_success_hook_failed",
                    slug=slug, error=str(exc),
                )

        # Lockout-state transitions for the audit trail.
        if cur.exceed_limit and not prev.exceed_limit:
            logger.warning(
                "screen_lock_watcher.touchscreen_locked",
                slug=slug, exceed_count=cur.exceed_count,
            )
        elif not cur.exceed_limit and prev.exceed_limit:
            logger.info(
                "screen_lock_watcher.touchscreen_unlocked", slug=slug,
            )
