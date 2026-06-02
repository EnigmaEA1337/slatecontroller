"""Pydantic IO models for devices."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DeviceStatus = Literal["pending", "adopted", "error"]
DeviceModel = Literal["slate-7-pro", "mudi-7", "other"]


class DeviceCreate(BaseModel):
    """Body of `POST /api/devices` — register a new device."""

    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    label: str = Field(default="", max_length=120)
    model: DeviceModel = "slate-7-pro"
    host: str = Field(min_length=3, max_length=120)
    rpc_port: int = Field(default=443, ge=1, le=65535)
    rpc_scheme: Literal["http", "https"] = "https"
    ssh_port: int = Field(default=22, ge=1, le=65535)
    rpc_username: str = Field(min_length=1, max_length=64)
    rpc_password: str = Field(min_length=1, max_length=256)
    notes: str = Field(default="", max_length=256)


class DeviceUpdate(BaseModel):
    """Body of `PATCH /api/devices/{slug}` — only mutable fields."""

    label: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, max_length=120)
    # Ordered list of admin URLs (LAN, Tailscale, WireGuard tunnel, IPv6,
    # custom). Used by the URL resolver for automatic failover. Accept
    # bare host (192.168.8.1, slate.taild2bce8.ts.net) or full URL
    # (https://host:port). Empty list = fall back to `host` legacy.
    admin_urls: list[str] | None = Field(default=None, max_length=10)
    rpc_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    rpc_username: str | None = Field(default=None, max_length=64)
    rpc_password: str | None = Field(default=None, max_length=256)
    notes: str | None = Field(default=None, max_length=256)


class DevicePublic(BaseModel):
    """Safe view of a device — never includes credentials."""

    id: int
    slug: str
    label: str
    model: str
    host: str
    admin_urls: list[str] = Field(default_factory=list)
    rpc_port: int
    rpc_scheme: str
    ssh_port: int
    rpc_username: str
    tls_fingerprint_sha256: str
    status: DeviceStatus
    is_default: bool
    notes: str
    last_probe_at: datetime | None
    adopted_at: datetime | None
    created_at: datetime
    # Convenience flags computed by the API layer.
    has_ssh_keypair: bool = False
    ssh_key_deployed: bool = False


class AdoptionOptions(BaseModel):
    """Which hardening tasks to run on adoption.

    Default = everything safe to do automatically. The user can untick from
    the UI before clicking 'Adopt'.

    Note: enabling LuCI access is NOT an option here — it's a prerequisite
    of every adoption (the controller relies on it for advanced debugging,
    and it costs nothing security-wise). See ``_task_enable_luci``.
    """

    pin_tls: bool = True
    force_https_webui: bool = True
    ssh_key_only: bool = True
    disable_upnp: bool = True


class AdoptionTaskReport(BaseModel):
    name: str
    status: Literal["pending", "running", "ok", "skipped", "failed"]
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AdoptionRunReport(BaseModel):
    """What `POST /devices/{slug}/adopt` returns. Synchronous for now."""

    device_slug: str
    overall_status: Literal["ok", "partial", "failed"]
    tasks: list[AdoptionTaskReport]


class FactoryResetConfirm(BaseModel):
    """Body of `POST /api/devices/{slug}/factory-reset`.

    Requires the operator to re-type the device slug as `confirm_slug`. This
    is an explicit destructive-action confirmation pattern (similar to
    GitHub's "type the repo name to delete it") since `firstboot` wipes the
    Slate's overlay and resets it to factory defaults — irreversible.
    """

    confirm_slug: str = Field(min_length=1, max_length=64)


class FactoryResetReport(BaseModel):
    device_slug: str
    started: bool
    note: str
