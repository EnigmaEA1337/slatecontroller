"""Tests for the /api/slate/status endpoint.

Stubs out the underlying `SlateClient` so tests never touch the network.
Fixtures are calibrated against real GL.iNet 4.8.x payloads.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.deps import get_slate_client
from app.exceptions import SlateUnreachableError
from app.main import app

# Fixtures mirror real responses from a GL-BE10000 on firmware 4.8.4.
_INFO_ENVELOPE: dict[str, Any] = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "model": "be10000",
        "firmware_version": "4.8.4",
        "firmware_type": "release",
        "vendor": "GL.iNet",
        "mac": "94:83:C4:DD:B7:59",
        "country_code": "DE",
        "cpu_num": 4,
        "board_info": {
            "hostname": "GL-BE10000",
            "model": "GL.iNet GL-BE10000",
        },
    },
}

_STATUS_ENVELOPE: dict[str, Any] = {
    "id": 2,
    "jsonrpc": "2.0",
    "result": {
        "system": {
            "uptime": 2144.65,
            "memory_total": 1_000_000,
            "memory_free": 400_000,
            "cpu": {"temperature": 68},
            "load_average": [0.24, 0.21, 0.25],
            "lan_ip": "192.168.8.1",
        },
        "network": [
            {"interface": "wan", "online": False, "up": False},
            {"interface": "wwan", "online": True, "up": True},
        ],
        "client": [{"cable_total": 1, "wireless_total": 4}],
        "service": [
            {"name": "adguard", "status": 0},
            {"name": "tor", "status": 1},
            {"name": "tailscale", "status": 1},
        ],
        "wifi": [
            {"ssid": "secret", "passwd": "TOP-SECRET-MUST-NOT-LEAK"},
        ],
    },
}


@pytest.fixture
async def fake_slate() -> AsyncIterator[AsyncMock]:
    mock = AsyncMock()
    app.dependency_overrides[get_slate_client] = lambda: mock
    yield mock
    app.dependency_overrides.clear()


async def test_status_maps_real_payload(
    client: AsyncClient, fake_slate: AsyncMock, bypass_auth: None
) -> None:
    """Parses both RPC payloads into the typed model with computed aggregates."""

    async def fake_call(group: str, method: str, _params: Any = None) -> dict[str, Any]:
        if (group, method) == ("system", "get_info"):
            return _INFO_ENVELOPE
        if (group, method) == ("system", "get_status"):
            return _STATUS_ENVELOPE
        raise AssertionError(f"unexpected {group}.{method}")

    fake_slate.call.side_effect = fake_call

    response = await client.get("/api/slate/status")
    assert response.status_code == 200
    p = response.json()

    assert p["connected"] is True
    assert p["model"] == "be10000"
    assert p["firmware_version"] == "4.8.4"
    assert p["hostname"] == "GL-BE10000"
    assert p["uptime_seconds"] == 2144.65
    assert p["memory_usage_percent"] == 60.0
    assert p["cpu_temperature_celsius"] == 68
    assert p["load_average_1m"] == 0.24
    assert p["connected_clients"] == 5
    assert p["wan_online"] is True
    assert p["services"] == {"adguard": False, "tor": True, "tailscale": True}


async def test_status_never_leaks_wifi_passwords(
    client: AsyncClient, fake_slate: AsyncMock, bypass_auth: None
) -> None:
    """The endpoint must not expose WiFi passwords from system.get_status.wifi[]."""

    async def fake_call(group: str, method: str, _params: Any = None) -> dict[str, Any]:
        if (group, method) == ("system", "get_info"):
            return _INFO_ENVELOPE
        if (group, method) == ("system", "get_status"):
            return _STATUS_ENVELOPE
        raise AssertionError(f"unexpected {group}.{method}")

    fake_slate.call.side_effect = fake_call

    response = await client.get("/api/slate/status")
    assert "TOP-SECRET-MUST-NOT-LEAK" not in response.text


async def test_status_partial_when_one_call_fails(
    client: AsyncClient, fake_slate: AsyncMock, bypass_auth: None
) -> None:
    """If get_status fails but get_info succeeds, we still return connected=True
    with whatever info fields we got."""
    from app.exceptions import SlateRpcError

    async def fake_call(group: str, method: str, _params: Any = None) -> dict[str, Any]:
        if (group, method) == ("system", "get_info"):
            return _INFO_ENVELOPE
        raise SlateRpcError("Method not found", group=group, method=method)

    fake_slate.call.side_effect = fake_call

    response = await client.get("/api/slate/status")
    assert response.status_code == 200
    p = response.json()
    assert p["connected"] is True
    assert p["firmware_version"] == "4.8.4"
    assert p["uptime_seconds"] is None
    assert p["connected_clients"] is None


async def test_status_unreachable_returns_503(
    client: AsyncClient, fake_slate: AsyncMock, bypass_auth: None
) -> None:
    fake_slate.call.side_effect = SlateUnreachableError("network down")
    response = await client.get("/api/slate/status")
    assert response.status_code == 503
    assert response.json()["detail"] == "Slate unreachable"
