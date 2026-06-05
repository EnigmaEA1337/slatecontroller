"""PIN bruteforce protection — 3 failed attempts within a 60s window
lock the PIN verifier for 60s, per (device, scope) tuple.

This module is the gatekeeper every PIN-protected flow has to walk
through :

  - call :meth:`check_or_raise` BEFORE comparing the user input ;
    it raises ``HTTPException(423 Locked)`` with a ``retry_after_s``
    field when the verifier is currently locked.
  - on a mismatch, call :meth:`record_failure` — that increments the
    counter ; if the threshold is hit the row gets ``locked_until``
    set 60s into the future.
  - on a match, call :meth:`record_success` to reset the counter.

State is persisted in the DB so a restart doesn't grant an attacker
"3 more tries". The rolling 60s window means a single failure on day 1
won't be held against the operator on day 2 — :meth:`record_failure`
zeroes the counter when ``last_attempt_at`` is older than the window.

Audit : every state transition (failure / lockout / success) is logged
via structlog at info level so the operator can grep ``pin_lockout.*``
for the timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import PinLockoutStateRow

logger = structlog.get_logger(__name__)


# Tunables — the user spec is 3 tries / 60s lockout. The rolling window
# is also 60s : failures spaced wider don't accumulate against you.
MAX_FAILURES = 3
LOCKOUT_S = 60
WINDOW_S = 60


@dataclass(frozen=True)
class LockoutState:
    """Snapshot of one (slug, scope) verifier — used by API responses."""

    failed_count: int
    locked_until: datetime | None
    remaining_attempts: int
    remaining_lock_s: int


class PinLockoutService:
    """Stateless service — every operation opens its own DB session.

    Cheap to instantiate ; lives on ``app.state.pin_lockout`` for the
    lifetime of the app. There's no in-memory cache — the table is tiny
    (one row per scope) and a 4-byte-per-attempt write is well below
    SQLite's noise floor.

    Optional ``anti_theft`` is wired by main.py after both services are
    constructed — every recorded outcome is forwarded so the autonomous
    mode can escalate beyond the per-window lockout.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory
        self._anti_theft = None  # type: ignore[assignment]

    def attach_anti_theft(self, anti_theft) -> None:  # noqa: ANN001
        """Wire the AntiTheftService (avoids an import cycle at module level)."""
        self._anti_theft = anti_theft

    async def snapshot(self, slug: str, scope: str) -> LockoutState:
        """Read-only view of the current state. Doesn't mutate anything."""
        async with self._sf() as s:
            row = await self._get(s, slug, scope)
            return self._project(row)

    async def check_or_raise(self, slug: str, scope: str) -> None:
        """Raise 423 if the verifier is currently locked. No mutation."""
        async with self._sf() as s:
            row = await self._get(s, slug, scope)
            if row is None:
                return
            now = datetime.now(UTC).replace(tzinfo=None)
            if row.locked_until is not None and row.locked_until > now:
                remaining = int((row.locked_until - now).total_seconds())
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED,
                    detail={
                        "message": "PIN verifier locked",
                        "retry_after_s": remaining,
                        "scope": scope,
                    },
                    headers={"Retry-After": str(remaining)},
                )

    async def record_failure(self, slug: str, scope: str) -> LockoutState:
        """Increment the counter, possibly locking the verifier."""
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sf() as s:
            row = await self._get_or_create(s, slug, scope)
            # Rolling window : if the last failure is stale, treat this
            # as a fresh streak rather than carrying the old count.
            if row.last_attempt_at is not None:
                window_cutoff = now - timedelta(seconds=WINDOW_S)
                if row.last_attempt_at < window_cutoff:
                    row.failed_count = 0
            row.failed_count = (row.failed_count or 0) + 1
            row.last_attempt_at = now
            locked_now = False
            if row.failed_count >= MAX_FAILURES:
                row.locked_until = now + timedelta(seconds=LOCKOUT_S)
                locked_now = True
            await s.commit()
            await s.refresh(row)
            snap = self._project(row)
        logger.info(
            "pin_lockout.failure",
            slug=slug, scope=scope,
            failed_count=snap.failed_count,
            locked=locked_now,
            remaining_attempts=snap.remaining_attempts,
        )
        # Forward to anti-theft AFTER the lockout state is persisted, so
        # an action fired here can rely on the lockout being recorded.
        if self._anti_theft is not None:
            try:
                await self._anti_theft.on_pin_failure(slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pin_lockout.anti_theft_hook_failed",
                    slug=slug, scope=scope, error=str(exc),
                )
        return snap

    async def record_success(self, slug: str, scope: str) -> LockoutState:
        """Reset the counter — successful verification wipes prior failures."""
        async with self._sf() as s:
            row = await self._get(s, slug, scope)
            if row is None:
                return LockoutState(
                    failed_count=0, locked_until=None,
                    remaining_attempts=MAX_FAILURES, remaining_lock_s=0,
                )
            row.failed_count = 0
            row.locked_until = None
            row.last_attempt_at = datetime.now(UTC).replace(tzinfo=None)
            await s.commit()
            await s.refresh(row)
            snap = self._project(row)
        logger.info(
            "pin_lockout.success", slug=slug, scope=scope,
        )
        # Successful verification clears the autonomous-mode cumulative
        # counter too — operator is back in control.
        if self._anti_theft is not None:
            try:
                await self._anti_theft.on_pin_success(slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pin_lockout.anti_theft_hook_failed",
                    slug=slug, scope=scope, error=str(exc),
                )
        return snap

    async def _get(
        self, s, slug: str, scope: str,
    ) -> PinLockoutStateRow | None:
        return await s.scalar(
            select(PinLockoutStateRow).where(
                PinLockoutStateRow.device_slug == slug,
                PinLockoutStateRow.scope == scope,
            ),
        )

    async def _get_or_create(
        self, s, slug: str, scope: str,
    ) -> PinLockoutStateRow:
        row = await self._get(s, slug, scope)
        if row is None:
            row = PinLockoutStateRow(
                device_slug=slug, scope=scope,
                failed_count=0, locked_until=None, last_attempt_at=None,
            )
            s.add(row)
            await s.flush()
        return row

    @staticmethod
    def _project(row: PinLockoutStateRow | None) -> LockoutState:
        if row is None:
            return LockoutState(
                failed_count=0, locked_until=None,
                remaining_attempts=MAX_FAILURES, remaining_lock_s=0,
            )
        now = datetime.now(UTC).replace(tzinfo=None)
        remaining_lock = 0
        if row.locked_until is not None and row.locked_until > now:
            remaining_lock = int((row.locked_until - now).total_seconds())
        remaining_att = max(0, MAX_FAILURES - (row.failed_count or 0))
        return LockoutState(
            failed_count=row.failed_count or 0,
            locked_until=row.locked_until,
            remaining_attempts=remaining_att,
            remaining_lock_s=remaining_lock,
        )
