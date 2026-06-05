"""Anti-theft configuration endpoints.

Operator-controlled : per device, toggle autonomous mode, pick threshold
+ action, see the cumulative counter. The 'test' endpoint is dry-run
only (NO data touched) so the UI can show what would fire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.devices.registry import DeviceConnections
from app.security.anti_theft import AntiTheftService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/security/anti-theft", tags=["security", "anti-theft"])


ActionLit = Literal["alert", "soft_wipe"]


class LockoutStateView(BaseModel):
    """Snapshot of the 3-tries/60s lockout for the default scope."""

    failed_count: int
    locked_until: datetime | None
    remaining_attempts: int
    remaining_lock_s: int


class TouchscreenLockoutView(BaseModel):
    """Snapshot of the on-device gl_screen lockout, polled via SSH."""

    continuous_errors: int
    exceed_count: int
    exceed_limit: bool
    last_polled_at: datetime | None
    last_error: str


class CombinedLockoutView(BaseModel):
    """Unified payload for the global banner — both verifiers in one shot."""

    controller: LockoutStateView
    touchscreen: TouchscreenLockoutView


class AntiTheftConfigView(BaseModel):
    autonomous_mode: bool
    failure_threshold: int
    action: ActionLit
    notify_webhook_url: str
    total_failures: int
    last_action_at: datetime | None
    last_action_kind: str
    last_action_note: str
    # Computed convenience field — how many failures remain before
    # action fires (0 = already on the edge, will fire on next miss).
    failures_until_trigger: int
    # Embedded so the UI gets the rolling-window lockout state in one
    # roundtrip — convenient for the gauge + countdown panel.
    lockout: LockoutStateView
    # Also include touchscreen telemetry so the page can show both
    # surfaces side-by-side.
    touchscreen: TouchscreenLockoutView


class AntiTheftConfigUpsert(BaseModel):
    autonomous_mode: bool
    failure_threshold: int = Field(ge=3, le=100)
    action: ActionLit
    notify_webhook_url: str = Field(default="", max_length=256)


class TestRunResult(BaseModel):
    summary: str


def _svc(request: Request) -> AntiTheftService:
    svc = getattr(request.app.state, "anti_theft", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="anti-theft service not initialised",
        )
    return svc


def _lockout_svc(request: Request):
    svc = getattr(request.app.state, "pin_lockout", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PIN lockout service not initialised",
        )
    return svc


def _touch_state(request: Request, slug: str):
    """Read the latest touchscreen snapshot from the watcher service."""
    watcher = getattr(request.app.state, "screen_lock_watcher", None)
    if watcher is None:
        # Service not yet wired (boot race) — return defaults.
        from app.scheduler.screen_lock_watcher import TouchscreenState
        return TouchscreenState()
    return watcher.get_state(slug)


@router.get("/lockout-status", response_model=CombinedLockoutView)
async def lockout_status(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> CombinedLockoutView:
    """Cheap polling endpoint for the global lockout banner — returns
    both verifiers (controller + touchscreen) in one shot."""
    snap = await _lockout_svc(request).snapshot(conn.slug, "controller_verify")
    touch = _touch_state(request, conn.slug)
    return CombinedLockoutView(
        controller=LockoutStateView(
            failed_count=snap.failed_count,
            locked_until=snap.locked_until,
            remaining_attempts=snap.remaining_attempts,
            remaining_lock_s=snap.remaining_lock_s,
        ),
        touchscreen=TouchscreenLockoutView(
            continuous_errors=touch.continuous_errors,
            exceed_count=touch.exceed_count,
            exceed_limit=touch.exceed_limit,
            last_polled_at=touch.last_polled_at,
            last_error=touch.last_error,
        ),
    )


def _to_view(snap, threshold: int, lockout, touch) -> AntiTheftConfigView:  # noqa: ANN001
    remaining = max(0, threshold - snap.total_failures)
    return AntiTheftConfigView(
        autonomous_mode=snap.autonomous_mode,
        failure_threshold=snap.failure_threshold,
        action=snap.action,
        notify_webhook_url=snap.notify_webhook_url,
        total_failures=snap.total_failures,
        last_action_at=snap.last_action_at,
        last_action_kind=snap.last_action_kind,
        last_action_note=snap.last_action_note,
        failures_until_trigger=remaining,
        lockout=LockoutStateView(
            failed_count=lockout.failed_count,
            locked_until=lockout.locked_until,
            remaining_attempts=lockout.remaining_attempts,
            remaining_lock_s=lockout.remaining_lock_s,
        ),
        touchscreen=TouchscreenLockoutView(
            continuous_errors=touch.continuous_errors,
            exceed_count=touch.exceed_count,
            exceed_limit=touch.exceed_limit,
            last_polled_at=touch.last_polled_at,
            last_error=touch.last_error,
        ),
    )


@router.get("", response_model=AntiTheftConfigView)
async def get_config(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> AntiTheftConfigView:
    """Return the current anti-theft policy (defaults if not configured)."""
    snap = await _svc(request).snapshot(conn.slug)
    lockout = await _lockout_svc(request).snapshot(conn.slug, "controller_verify")
    touch = _touch_state(request, conn.slug)
    return _to_view(snap, snap.failure_threshold, lockout, touch)


@router.put("", response_model=AntiTheftConfigView)
async def update_config(
    body: AntiTheftConfigUpsert,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> AntiTheftConfigView:
    """Create or update the anti-theft policy."""
    snap = await _svc(request).upsert(
        conn.slug,
        autonomous_mode=body.autonomous_mode,
        failure_threshold=body.failure_threshold,
        action=body.action,
        notify_webhook_url=body.notify_webhook_url,
    )
    logger.info(
        "anti_theft.config.updated",
        username=user.username, device=conn.slug,
        autonomous=body.autonomous_mode,
        threshold=body.failure_threshold, action=body.action,
    )
    lockout = await _lockout_svc(request).snapshot(conn.slug, "controller_verify")
    touch = _touch_state(request, conn.slug)
    return _to_view(snap, snap.failure_threshold, lockout, touch)


@router.post("/reset-counter", response_model=AntiTheftConfigView)
async def reset_counter(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> AntiTheftConfigView:
    """Manually reset the cumulative failure counter. Operator
    intervention after a legit recovery (forgot the PIN momentarily)."""
    snap = await _svc(request).reset_counter(conn.slug)
    logger.info(
        "anti_theft.counter.reset",
        username=user.username, device=conn.slug,
    )
    lockout = await _lockout_svc(request).snapshot(conn.slug, "controller_verify")
    touch = _touch_state(request, conn.slug)
    return _to_view(snap, snap.failure_threshold, lockout, touch)


@router.post("/test", response_model=TestRunResult)
async def test_run(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> TestRunResult:
    """Dry-run the configured action. Never touches data — just returns
    a human summary of what would happen."""
    summary = await _svc(request).test_run(conn.slug)
    logger.info(
        "anti_theft.test_run",
        username=user.username, device=conn.slug,
    )
    return TestRunResult(summary=summary)
