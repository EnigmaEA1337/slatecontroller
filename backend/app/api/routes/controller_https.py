"""HTTP routes for the controller's own HTTPS access (via Tailscale Serve).

Reads + manages the host's tailscaled Serve config so the operator can
toggle HTTPS for this very controller from its own UI.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import User, get_current_user
from app.settings.controller_https import (
    ControllerHttpsState,
    disable_https,
    enable_https,
    get_state,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/settings/controller-https", tags=["settings"])


class CertView(BaseModel):
    issuer: str | None = None
    not_after: str | None = None
    days_remaining: int | None = None


class RouteView(BaseModel):
    path: str
    target: str


class ControllerHttpsView(BaseModel):
    cli_available: bool
    daemon_reachable: bool
    operator_set: bool
    tailnet_hostname: str | None
    tailnet_name: str | None
    tailscale_ips: list[str]
    https_enabled: bool
    routes: list[RouteView]
    cert: CertView | None
    public_url: str | None
    raw_error: str | None
    feature_https_enabled_in_admin: bool | None


def _to_view(s: ControllerHttpsState) -> ControllerHttpsView:
    return ControllerHttpsView(
        cli_available=s.cli_available,
        daemon_reachable=s.daemon_reachable,
        operator_set=s.operator_set,
        tailnet_hostname=s.tailnet_hostname,
        tailnet_name=s.tailnet_name,
        tailscale_ips=s.tailscale_ips,
        https_enabled=s.https_enabled,
        routes=[RouteView(path=r.path, target=r.target) for r in s.routes],
        cert=(
            CertView(
                issuer=s.cert.issuer,
                not_after=s.cert.not_after.isoformat() if s.cert.not_after else None,
                days_remaining=s.cert.days_remaining,
            )
            if s.cert is not None
            else None
        ),
        public_url=s.public_url,
        raw_error=s.raw_error,
        feature_https_enabled_in_admin=s.feature_https_enabled_in_admin,
    )


@router.get("", response_model=ControllerHttpsView)
async def get_controller_https_state(
    _user: Annotated[User, Depends(get_current_user)],
) -> ControllerHttpsView:
    """Snapshot of the controller's HTTPS posture.

    Always returns 200 — the snapshot itself carries the failure
    flags (cli_available / daemon_reachable / raw_error) so the UI
    can render actionable instructions instead of an opaque error
    page.
    """
    state = await get_state()
    return _to_view(state)


class WriteResponse(BaseModel):
    ok: bool
    message: str
    operator_hint: bool = False


@router.post("/enable", response_model=WriteResponse)
async def enable_controller_https(
    user: Annotated[User, Depends(get_current_user)],
) -> WriteResponse:
    res = await enable_https()
    logger.info(
        "controller_https.enable",
        username=user.username,
        ok=res.ok,
        message=res.message,
    )
    if not res.ok:
        # Surface as 400 with the message so the UI can show the
        # operator-setup hint inline, not as an opaque "Internal error".
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": res.message, "operator_hint": res.operator_hint},
        )
    return WriteResponse(ok=True, message=res.message)


@router.post("/disable", response_model=WriteResponse)
async def disable_controller_https(
    user: Annotated[User, Depends(get_current_user)],
) -> WriteResponse:
    res = await disable_https()
    logger.info(
        "controller_https.disable",
        username=user.username,
        ok=res.ok,
        message=res.message,
    )
    if not res.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": res.message, "operator_hint": res.operator_hint},
        )
    return WriteResponse(ok=True, message=res.message)
