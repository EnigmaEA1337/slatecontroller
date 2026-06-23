"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.auth import (
    TokenResponse,
    User,
    authenticate,
    create_access_token,
    get_current_user,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Bruteforce gate (nightly audit 2026-06-23 high finding) : the same
# PinLockoutService that gates touchscreen PIN verification on the Slate
# is wired here to throttle credential bruteforce against the controller
# admin login. The scope keeps the counters independent from any device
# screen-lock counters (so a locked phone doesn't also lock the login
# page). The "slug" is the client IP so a single rogue host gets locked
# instead of the global login surface — multiple operators on a LAN keep
# working unless their own IP is in the bad-streak window.
_LOGIN_SCOPE = "controller_login"


def _client_ip(request: Request) -> str:
    """Best-effort client IP for the lockout key.

    Falls back to ``"unknown"`` when no source IP can be derived (e.g.
    test client without ASGI scope). The string only feeds the per-key
    counter — security depends on the lockout window, not the IP being
    cryptographically authentic.
    """
    if request.client and request.client.host:
        return request.client.host
    fwd = request.headers.get("x-forwarded-for") or ""
    return fwd.split(",")[0].strip() or "unknown"


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
) -> TokenResponse:
    """Exchange username/password for a JWT.

    Uses the OAuth2 password flow (form-encoded body with `username`,
    `password`). Throttled per-IP via PinLockoutService — after 3
    consecutive failures within the rolling window the endpoint returns
    423 Locked with a Retry-After header until the cooldown elapses.
    """
    lockout = getattr(request.app.state, "pin_lockout", None)
    ip = _client_ip(request)

    # Check before doing any expensive work (auth handler reads from DB).
    if lockout is not None:
        await lockout.check_or_raise(ip, _LOGIN_SCOPE)

    user = authenticate(form_data.username, form_data.password)
    if user is None:
        if lockout is not None:
            snap = await lockout.record_failure(ip, _LOGIN_SCOPE)
            logger.info(
                "auth.login.denied",
                username=form_data.username,
                client_ip=ip,
                failed_count=snap.failed_count,
                remaining_attempts=snap.remaining_attempts,
            )
        else:
            logger.info("auth.login.denied", username=form_data.username, client_ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if lockout is not None:
        await lockout.record_success(ip, _LOGIN_SCOPE)
    logger.info("auth.login.ok", username=user.username, client_ip=ip)
    return create_access_token(user.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current_user: Annotated[User, Depends(get_current_user)]) -> None:
    """Stateless: the client must discard its token.

    This endpoint exists for API symmetry and to confirm the caller's token
    is valid at logout time (audit trail).
    """
    logger.info("auth.logout", username=current_user.username)


@router.get("/me", response_model=User)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user
