"""End-to-end tests for /api/profiles CRUD against a real SQLite DB."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_profile_store
from app.db.database import Base
from app.main import app
from app.models.profile import Profile
from app.profiles.store import ProfileStore


def _make_profile(name: str, **extras: object) -> dict:
    base: dict = {
        "name": name,
        "description": f"profile {name}",
        "vpn": {"type": "none", "kill_switch": False},
    }
    base.update(extras)
    return base


@pytest_asyncio.fixture
async def store_with_seed(tmp_path: Path) -> AsyncIterator[ProfileStore]:
    """Per-test fresh SQLite, seeded with two profiles (one template, one user)."""
    from app.db import models  # noqa: F401

    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    store = ProfileStore(session_factory)

    await store.seed_from(
        [
            Profile.model_validate(
                _make_profile(
                    "mission",
                    description="Template mission",
                    vpn={"type": "wireguard", "client": "corp", "kill_switch": True},
                )
            ),
        ]
    )
    await store.create(
        Profile.model_validate(_make_profile("custom-user", description="From user")),
        source="user",
    )

    app.dependency_overrides[get_profile_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_profile_store, None)
    await engine.dispose()


# ---------------------------- list ---------------------------- #


async def test_list_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/profiles")
    assert resp.status_code == 401


async def test_list_returns_envelopes_with_source(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.get("/api/profiles")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    by_name = {item["profile"]["name"]: item for item in items}
    assert by_name["mission"]["source"] == "template"
    assert by_name["custom-user"]["source"] == "user"
    assert all(item["is_active"] is False for item in items)


# ---------------------------- get one ---------------------------- #


async def test_get_returns_envelope(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.get("/api/profiles/mission")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["vpn"]["client"] == "corp"
    assert body["source"] == "template"


async def test_get_404(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.get("/api/profiles/nope")
    assert resp.status_code == 404


# ---------------------------- create ---------------------------- #


async def test_create_succeeds(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.post(
        "/api/profiles", json=_make_profile("brand-new", description="hello")
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "user"
    assert body["profile"]["name"] == "brand-new"


async def test_create_rejects_duplicate(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.post("/api/profiles", json=_make_profile("mission"))
    assert resp.status_code == 409


async def test_create_rejects_bad_schema(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.post(
        "/api/profiles", json={"name": "x", "unknown_field": 42}
    )
    assert resp.status_code == 422


# ---------------------------- update ---------------------------- #


async def test_update_overwrites_payload(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    payload = _make_profile(
        "custom-user", description="now updated", icon="palmtree"
    )
    resp = await client.put("/api/profiles/custom-user", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["description"] == "now updated"
    assert body["profile"]["icon"] == "palmtree"


async def test_update_404(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.put(
        "/api/profiles/nope", json=_make_profile("nope")
    )
    assert resp.status_code == 404


# ---------------------------- delete ---------------------------- #


async def test_delete_user_profile(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.delete("/api/profiles/custom-user")
    assert resp.status_code == 204


async def test_delete_template_blocked(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.delete("/api/profiles/mission")
    assert resp.status_code == 409
    assert "template" in resp.json()["detail"].lower()


# ---------------------------- duplicate ---------------------------- #


async def test_duplicate_clones_to_new_name(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.post(
        "/api/profiles/mission/duplicate", json={"new_name": "my-mission"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "user"
    assert body["profile"]["name"] == "my-mission"
    assert body["profile"]["vpn"]["kill_switch"] is True


# ---------------------------- activate ---------------------------- #


async def test_active_endpoint_returns_null_initially(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.get("/api/profiles/active")
    assert resp.status_code == 200
    assert resp.json() == {"active_name": None, "profile": None}


async def test_activate_sets_marker(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.post("/api/profiles/mission/activate")
    assert resp.status_code == 200
    assert resp.json()["active_name"] == "mission"

    # Subsequent calls reflect it
    resp = await client.get("/api/profiles/active")
    assert resp.json()["active_name"] == "mission"
    resp = await client.get("/api/profiles/mission")
    assert resp.json()["is_active"] is True


async def test_activate_404(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    resp = await client.post("/api/profiles/nope/activate")
    assert resp.status_code == 404


async def test_deleting_active_profile_clears_marker(
    client: AsyncClient, store_with_seed: ProfileStore, bypass_auth: None
) -> None:
    await client.post("/api/profiles/custom-user/activate")
    await client.delete("/api/profiles/custom-user")
    resp = await client.get("/api/profiles/active")
    assert resp.json() == {"active_name": None, "profile": None}
