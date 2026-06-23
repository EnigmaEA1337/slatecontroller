"""JWT authentication.

MVP design: a single admin user defined by `ADMIN_USERNAME` / `ADMIN_PASSWORD`
in the environment. No user table, no password hashing in storage (the env
var IS the source of truth). When we add multi-user support (V2), introduce
bcrypt and a `users` table.

The login uses `secrets.compare_digest` for timing-safe string comparison.
Tokens are signed JWTs (HS256 by default) carrying only `sub` and `exp`.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import Settings, get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=True)


class User(BaseModel):
    """Authenticated principal. Minimal for MVP."""

    username: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _sha256(data: str) -> bytes:
    """Constant-length (32 bytes) digest used by :func:`authenticate`.

    ``secrets.compare_digest`` only runs in constant time when both
    inputs share the same length — feeding raw user-supplied bytes
    against the admin secret leaks the secret's length when the user
    types something of different size (nightly audit 2026-06-23 low).
    Hashing both sides first removes the length channel entirely.
    """
    import hashlib

    return hashlib.sha256(data.encode("utf-8")).digest()


def authenticate(username: str, password: str, settings: Settings | None = None) -> User | None:
    """Validate credentials against the configured admin.

    Uses constant-time comparison over fixed-length SHA256 digests to
    avoid leaking username/password length via timing side-channels.
    Returns ``None`` on any mismatch.
    """
    s = settings or get_settings()
    if not (
        secrets.compare_digest(_sha256(username), _sha256(s.admin_username))
        & secrets.compare_digest(_sha256(password), _sha256(s.admin_password))
    ):
        return None
    return User(username=username)


def create_access_token(username: str, settings: Settings | None = None) -> TokenResponse:
    """Mint a signed JWT for ``username``.

    The token carries an ``iat`` claim (issued-at) so a future revoke-on-
    timestamp policy can invalidate everything older than a given epoch
    (e.g. after a password rotation) — nightly audit 2026-06-23 low.
    Without iat, the only revoke knob is letting ``exp`` lapse, which
    means leaked tokens stay valid for ``JWT_EXPIRATION_HOURS``.
    """
    s = settings or get_settings()
    expires_delta = timedelta(hours=s.jwt_expiration_hours)
    now = datetime.now(UTC)
    expire = now + expires_delta
    payload: dict[str, Any] = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": expire,
    }
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return TokenResponse(access_token=token, expires_in=int(expires_delta.total_seconds()))


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    """Decode the JWT from `Authorization: Bearer <token>` and return the user."""
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise creds_exc from exc
    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        raise creds_exc
    return User(username=username)
