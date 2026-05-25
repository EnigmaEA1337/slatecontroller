"""Proton VPN routes — authentication for now (servers/configs later)."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_proton_client
from app.auth import User, get_current_user
from app.vpn.proton_client import (
    ProtonAuthError,
    ProtonAuthState,
    ProtonClient,
    ProtonNotLoggedInError,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/vpn/proton", tags=["proton"])


# ---------------------------- request / response ---------------------------- #


class ProtonLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ProtonTwoFactorRequest(BaseModel):
    code: str = Field(min_length=4, max_length=10, description="6-digit TOTP code")


class ProtonStatusResponse(BaseModel):
    authenticated: bool
    two_factor_pending: bool

    @classmethod
    def from_state(cls, state: ProtonAuthState) -> ProtonStatusResponse:
        return cls(
            authenticated=state.authenticated,
            two_factor_pending=state.two_factor_pending,
        )


# ---------------------------- endpoints ---------------------------- #


@router.get("/auth/status", response_model=ProtonStatusResponse)
async def status_endpoint(
    proton: Annotated[ProtonClient, Depends(get_proton_client)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ProtonStatusResponse:
    """Return whether the backend currently holds a valid Proton session."""
    return ProtonStatusResponse.from_state(proton.state())


@router.post("/auth/login", response_model=ProtonStatusResponse)
async def login_endpoint(
    body: ProtonLoginRequest,
    proton: Annotated[ProtonClient, Depends(get_proton_client)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ProtonStatusResponse:
    """Authenticate against Proton with username + password.

    Returns 200 with `two_factor_pending=true` if a TOTP code is now required.
    Returns 401 on bad credentials.
    """
    try:
        state = await proton.login(body.username, body.password)
    except ProtonAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return ProtonStatusResponse.from_state(state)


@router.post("/auth/2fa", response_model=ProtonStatusResponse)
async def two_factor_endpoint(
    body: ProtonTwoFactorRequest,
    proton: Annotated[ProtonClient, Depends(get_proton_client)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> ProtonStatusResponse:
    """Submit a TOTP code to elevate an `awaiting 2FA` session."""
    try:
        state = await proton.submit_two_factor(body.code)
    except ProtonNotLoggedInError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="login first",
        ) from exc
    except ProtonAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return ProtonStatusResponse.from_state(state)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_endpoint(
    proton: Annotated[ProtonClient, Depends(get_proton_client)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Drop the current Proton session (best-effort, never errors)."""
    await proton.logout()
