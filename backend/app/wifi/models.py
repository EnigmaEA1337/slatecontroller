"""Pydantic models for the Wi-Fi SSID catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 2.4/5/6 GHz are the discrete radios. MLO bundles them for Wi-Fi 7 clients.
WifiBand = Literal["2GHz", "5GHz", "6GHz", "MLO"]
WifiSecurity = Literal["WPA3-SAE", "WPA3-PSK", "WPA2-PSK", "WPA2-WPA3-Mixed", "open"]


class WifiSsidPublic(BaseModel):
    """SSID record exposed via the API — password never included."""

    slug: str
    ssid_name: str
    band: WifiBand
    security: WifiSecurity
    network_slug: str
    client_isolation: bool
    notes: str
    has_password: bool
    created_at: datetime
    updated_at: datetime


class WifiSsidWrite(BaseModel):
    """Request body for both create and update.

    `password` is optional: `None` on update means "don't touch the existing
    password" (so the UI doesn't need to round-trip secrets); `""` clears it.
    On create, `None` means "no password" (only valid for `open`).
    """

    model_config = ConfigDict(extra="forbid")

    ssid_name: str = Field(min_length=1, max_length=32)
    band: WifiBand = "5GHz"
    security: WifiSecurity = "WPA3-SAE"
    password: str | None = Field(default=None, max_length=128)
    network_slug: str = Field(
        default="lan",
        min_length=1,
        max_length=64,
        description="References Network.slug (which bridge/subnet the SSID lives on).",
    )
    client_isolation: bool = Field(
        default=False,
        description="If True, clients within this SSID cannot talk to each other.",
    )
    notes: str = Field(default="", max_length=256)


class WifiSsidCreate(WifiSsidWrite):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
