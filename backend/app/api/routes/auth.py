"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
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


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    """Exchange username/password for a JWT.

    Uses the OAuth2 password flow (form-encoded body with `username`, `password`).
    """
    user = authenticate(form_data.username, form_data.password)
    if user is None:
        logger.info("auth.login.denied", username=form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    logger.info("auth.login.ok", username=user.username)
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
