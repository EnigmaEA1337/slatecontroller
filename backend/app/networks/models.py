"""Pydantic models for the Networks catalog (subnets / VLANs)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NetworkPublic(BaseModel):
    """Network record as exposed by the API."""

    slug: str
    display_name: str
    bridge_name: str
    subnet_cidr: str
    gateway_ip: str
    dhcp_enabled: bool
    isolated_from_lan: bool
    vlan_tag: int | None
    is_builtin: bool
    notes: str
    ipv6_enabled: bool
    ipv6_subnet_cidr: str
    created_at: datetime
    updated_at: datetime


class NetworkWrite(BaseModel):
    """Request body for create + update (PUT is full replacement)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=64)
    bridge_name: str = Field(
        min_length=1, max_length=32, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    subnet_cidr: str = Field(min_length=9, max_length=32)
    gateway_ip: str = Field(default="", max_length=40)
    dhcp_enabled: bool = True
    isolated_from_lan: bool = False
    vlan_tag: int | None = Field(default=None, ge=1, le=4094)
    notes: str = Field(default="", max_length=256)
    ipv6_enabled: bool = Field(
        default=False, description="If True, IPv6 is active on this bridge."
    )
    ipv6_subnet_cidr: str = Field(
        default="",
        max_length=64,
        description=(
            "Static IPv6 prefix (e.g. 'fd00:abcd:1234::/64'). Leave empty to use "
            "SLAAC + Prefix Delegation from the WAN."
        ),
    )

    @field_validator("subnet_cidr")
    @classmethod
    def _validate_cidr(cls, value: str) -> str:
        # Cheap CIDR shape check; full validation happens at the SQLite ipam layer.
        if "/" not in value:
            raise ValueError("subnet_cidr must be in CIDR notation (e.g. 192.168.8.0/24)")
        addr, _, mask = value.partition("/")
        parts = addr.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            raise ValueError("subnet_cidr address part must be IPv4 dotted-decimal")
        if not mask.isdigit() or not 0 <= int(mask) <= 32:
            raise ValueError("subnet_cidr prefix length must be 0-32")
        return value

    @field_validator("ipv6_subnet_cidr")
    @classmethod
    def _validate_ipv6_cidr(cls, value: str) -> str:
        # Empty is valid (meaning "auto via WAN delegation"). Otherwise expect CIDR.
        if not value:
            return value
        if "/" not in value:
            raise ValueError(
                "ipv6_subnet_cidr must be a CIDR (e.g. 'fd00:abcd:1234::/64') or empty"
            )
        addr, _, mask = value.partition("/")
        if ":" not in addr:
            raise ValueError("ipv6_subnet_cidr address part must contain ':'")
        if not mask.isdigit() or not 0 <= int(mask) <= 128:
            raise ValueError("ipv6_subnet_cidr prefix length must be 0-128")
        return value


class NetworkCreate(NetworkWrite):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
