"""Pydantic models for VPN config upload and retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

VpnProvider = Literal["proton", "other"]


class VPNConfigPublic(BaseModel):
    """Sanitized view of a stored config — no private key.

    Returned by `GET /api/vpn/configs` and friends.
    """

    name: str
    provider: VpnProvider
    interface_address: str
    dns_servers: list[str]
    peer_public_key: str
    peer_endpoint: str
    peer_allowed_ips: list[str]
    created_at: datetime


class VPNConfigUploadResponse(BaseModel):
    """Returned after a successful upload."""

    name: str = Field(description="Slug under which the config is stored.")
    provider: VpnProvider
    peer_endpoint: str
