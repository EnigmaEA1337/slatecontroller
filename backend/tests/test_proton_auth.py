"""Tests for the Proton auth endpoints (mocked Proton API)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.api.deps import get_proton_client
from app.main import app
from app.vpn.proton_client import (
    ProtonAuthError,
    ProtonAuthState,
    ProtonNotLoggedInError,
)


@pytest.fixture
async def fake_proton() -> AsyncIterator[AsyncMock]:
    """AsyncMock with `.state()` overridden to be sync (matches real class)."""
    mock = AsyncMock()
    mock.state = MagicMock(
        return_value=ProtonAuthState(authenticated=False, two_factor_pending=False)
    )
    app.dependency_overrides[get_proton_client] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_proton_client, None)


# ---------------------------- status ---------------------------- #


async def test_status_requires_app_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/vpn/proton/auth/status")
    assert resp.status_code == 401


async def test_status_returns_state(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.state = MagicMock(
        return_value=ProtonAuthState(authenticated=True, two_factor_pending=False)
    )
    resp = await client.get("/api/vpn/proton/auth/status")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True, "two_factor_pending": False}


# ---------------------------- login ---------------------------- #


async def test_login_success_no_2fa(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.login.return_value = ProtonAuthState(
        authenticated=True, two_factor_pending=False
    )
    resp = await client.post(
        "/api/vpn/proton/auth/login",
        json={"username": "user@proton.me", "password": "secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True, "two_factor_pending": False}
    fake_proton.login.assert_awaited_once_with("user@proton.me", "secret")


async def test_login_success_pending_2fa(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.login.return_value = ProtonAuthState(
        authenticated=False, two_factor_pending=True
    )
    resp = await client.post(
        "/api/vpn/proton/auth/login",
        json={"username": "user@proton.me", "password": "secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False, "two_factor_pending": True}


async def test_login_bad_credentials(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.login.side_effect = ProtonAuthError("[Proton 8002] Wrong password")
    resp = await client.post(
        "/api/vpn/proton/auth/login",
        json={"username": "user@proton.me", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert "Wrong password" in resp.json()["detail"]


# ---------------------------- 2fa ---------------------------- #


async def test_2fa_success(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.submit_two_factor.return_value = ProtonAuthState(
        authenticated=True, two_factor_pending=False
    )
    resp = await client.post(
        "/api/vpn/proton/auth/2fa", json={"code": "123456"}
    )
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


async def test_2fa_wrong_code(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.submit_two_factor.side_effect = ProtonAuthError("Invalid TOTP")
    resp = await client.post(
        "/api/vpn/proton/auth/2fa", json={"code": "000000"}
    )
    assert resp.status_code == 401


async def test_2fa_without_login_returns_400(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    fake_proton.submit_two_factor.side_effect = ProtonNotLoggedInError(
        "login first"
    )
    resp = await client.post(
        "/api/vpn/proton/auth/2fa", json={"code": "123456"}
    )
    assert resp.status_code == 400


# ---------------------------- logout ---------------------------- #


async def test_logout_returns_204(
    client: AsyncClient, fake_proton: AsyncMock, bypass_auth: None
) -> None:
    resp = await client.post("/api/vpn/proton/auth/logout")
    assert resp.status_code == 204
    fake_proton.logout.assert_awaited_once()
