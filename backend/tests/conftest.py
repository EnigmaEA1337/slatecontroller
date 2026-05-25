"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import User, get_current_user
from app.config import get_settings
from app.main import app
from app.profiles.store import ProfileStore
from app.slate.client import SlateClient
from app.slate.profiles import ProfileManager
from app.slate.ssh import SlateSSH
from app.vpn.configs_store import VPNConfigStore
from app.vpn.proton_client import ProtonClient
from app.wifi.store import WifiSsidStore


@pytest.fixture(autouse=True)
def stub_app_state() -> Iterator[None]:
    """Lifespan isn't run by ASGITransport, so populate app.state ourselves.

    Provides mocks/real instances for the singletons normally constructed by
    `lifespan`. Tests that need to override behavior use
    `app.dependency_overrides`.
    """
    app.state.slate_client = AsyncMock(spec=SlateClient)
    app.state.slate_ssh = AsyncMock(spec=SlateSSH)
    app.state.profile_manager = ProfileManager(get_settings().profiles_dir)
    app.state.proton_client = AsyncMock(spec=ProtonClient)
    app.state.vpn_config_store = AsyncMock(spec=VPNConfigStore)
    app.state.profile_store = AsyncMock(spec=ProfileStore)
    wifi_mock = AsyncMock(spec=WifiSsidStore)
    wifi_mock.list_all.return_value = []
    wifi_mock.get_password.return_value = ""
    app.state.wifi_store = wifi_mock
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTPX client bound to the FastAPI app (no network)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def bypass_auth() -> Iterator[None]:
    """Override the `get_current_user` dep so protected routes accept anything.

    Use in tests that exercise route logic (not auth itself).
    """
    app.dependency_overrides[get_current_user] = lambda: User(username="test")
    yield
    app.dependency_overrides.pop(get_current_user, None)
