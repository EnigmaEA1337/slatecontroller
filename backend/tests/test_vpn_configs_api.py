"""End-to-end tests for /api/vpn/configs endpoints (with a real SQLite DB)."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_vpn_config_store
from app.db.database import Base
from app.main import app
from app.vpn.configs_store import VPNConfigStore

SAMPLE_CONF = b"""\
[Interface]
PrivateKey = oFAEYO9j7gNQwAAr3pe9LH+UMyqoLBHRzkS+QCDuHWY=
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = CzogIfsr1lHt6QmaTOoLnBhJ7Z3vIflxK0w0pUYIY0o=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = node-fr-12.protonvpn.net:51820
"""


@pytest_asyncio.fixture
async def store_with_temp_db(tmp_path: Path) -> AsyncIterator[VPNConfigStore]:
    """Spin up a fresh per-test SQLite, attach the store override."""
    # Ensure ORM models are imported and registered on Base.metadata.
    from app.db import models  # noqa: F401

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    store = VPNConfigStore(session_factory)

    app.dependency_overrides[get_vpn_config_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_vpn_config_store, None)
    await engine.dispose()


async def _upload(
    client: AsyncClient,
    name: str,
    *,
    body: bytes = SAMPLE_CONF,
    provider: str = "proton",
) -> tuple[int, dict]:
    files = {"file": (f"{name}.conf", io.BytesIO(body), "application/x-wireguard-conf")}
    resp = await client.post(
        "/api/vpn/configs",
        files=files,
        data={"name": name, "provider": provider},
    )
    try:
        body_json = resp.json()
    except ValueError:
        body_json = {"_raw": resp.text}
    return resp.status_code, body_json


async def test_list_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/vpn/configs")
    assert resp.status_code == 401


async def test_upload_then_list_then_delete(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    code, body = await _upload(client, "proton-fr-12")
    assert code == 201
    assert body["name"] == "proton-fr-12"
    assert body["peer_endpoint"] == "node-fr-12.protonvpn.net:51820"

    # List should contain it (no private key)
    resp = await client.get("/api/vpn/configs")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    listed = items[0]
    assert listed["name"] == "proton-fr-12"
    assert listed["interface_address"] == "10.2.0.2/32"
    assert "oFAEYO9j7gNQ" not in resp.text  # private key never returned

    # Single get
    resp = await client.get("/api/vpn/configs/proton-fr-12")
    assert resp.status_code == 200
    assert resp.json()["peer_public_key"].endswith("Y0o=")

    # Delete
    resp = await client.delete("/api/vpn/configs/proton-fr-12")
    assert resp.status_code == 204

    # Now empty
    resp = await client.get("/api/vpn/configs")
    assert resp.json() == []


async def test_private_key_decryptable_via_store(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    """Even though the API never exposes the private key, the store can recover it."""
    code, _ = await _upload(client, "fr-12-priv")
    assert code == 201
    private_key = await store_with_temp_db.get_private_key("fr-12-priv")
    assert private_key == "oFAEYO9j7gNQwAAr3pe9LH+UMyqoLBHRzkS+QCDuHWY="


async def test_upload_rejects_garbage(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    code, body = await _upload(client, "broken", body=b"not a wireguard config")
    assert code == 400
    assert "invalid wireguard config" in body["detail"].lower()


async def test_upload_rejects_duplicate(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    code, _ = await _upload(client, "dup")
    assert code == 201
    code, body = await _upload(client, "dup")
    assert code == 409
    assert "already exists" in body["detail"]


async def test_upload_slugs_messy_name(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    code, body = await _upload(client, "  Proton FR #12  ")
    assert code == 201
    assert body["name"] == "proton-fr-12"


async def test_get_404(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    resp = await client.get("/api/vpn/configs/nope")
    assert resp.status_code == 404


async def test_delete_404(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    resp = await client.delete("/api/vpn/configs/nope")
    assert resp.status_code == 404


async def test_upload_too_large(
    client: AsyncClient, store_with_temp_db: VPNConfigStore, bypass_auth: None
) -> None:
    big = b"X" * (16 * 1024 + 1)
    code, _ = await _upload(client, "huge", body=big)
    assert code == 413
