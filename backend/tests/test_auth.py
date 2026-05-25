"""Tests for the /api/auth/* endpoints and JWT machinery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from jose import jwt

from app.config import get_settings

# Defaults from app.config.Settings (no .env loaded in tests).
ADMIN_USER = "admin"
ADMIN_PASSWORD = "change-me"


async def _login(client: AsyncClient, username: str, password: str) -> tuple[int, dict]:
    """Hit /api/auth/login with form-encoded creds."""
    resp = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    return resp.status_code, resp.json()


async def test_login_returns_jwt_with_valid_credentials(client: AsyncClient) -> None:
    code, body = await _login(client, ADMIN_USER, ADMIN_PASSWORD)
    assert code == 200
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"].count(".") == 2
    assert body["expires_in"] > 0


@pytest.mark.parametrize(
    ("username", "password"),
    [
        (ADMIN_USER, "wrong"),
        ("wrong", ADMIN_PASSWORD),
        ("wrong", "wrong"),
    ],
)
async def test_login_rejects_bad_credentials(
    client: AsyncClient, username: str, password: str
) -> None:
    code, body = await _login(client, username, password)
    assert code == 401
    assert body["detail"] == "Invalid credentials"


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_with_valid_token(client: AsyncClient) -> None:
    _, login_body = await _login(client, ADMIN_USER, ADMIN_PASSWORD)
    resp = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login_body['access_token']}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"username": ADMIN_USER}


async def test_me_with_invalid_token(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert resp.status_code == 401


async def test_me_with_expired_token(client: AsyncClient) -> None:
    settings = get_settings()
    expired = jwt.encode(
        {"sub": ADMIN_USER, "exp": datetime.now(UTC) - timedelta(seconds=10)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    resp = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert resp.status_code == 401


async def test_me_with_token_signed_by_wrong_secret(client: AsyncClient) -> None:
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "evil", "exp": datetime.now(UTC) + timedelta(hours=1)},
        "wrong-secret",
        algorithm=settings.jwt_algorithm,
    )
    resp = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert resp.status_code == 401


async def test_logout_requires_token(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 401


async def test_logout_with_token_returns_204(client: AsyncClient) -> None:
    _, login_body = await _login(client, ADMIN_USER, ADMIN_PASSWORD)
    resp = await client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {login_body['access_token']}"},
    )
    assert resp.status_code == 204


async def test_slate_status_requires_auth(client: AsyncClient) -> None:
    """Regression: /api/slate/status must NOT be reachable without a token."""
    resp = await client.get("/api/slate/status")
    assert resp.status_code == 401
