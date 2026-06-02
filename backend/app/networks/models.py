"""Pydantic models for the Networks catalog (subnets / VLANs)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TorRouteMode = Literal["off", "transparent", "socks_only"]


class NetworkPublic(BaseModel):
    """Network record as exposed by the API."""

    slug: str
    display_name: str
    bridge_name: str
    subnet_cidr: str
    gateway_ip: str
    dhcp_enabled: bool
    vlan_tag: int | None
    notes: str
    ipv6_enabled: bool
    ipv6_subnet_cidr: str

    # Isolation model. See db/models.py NetworkRow for the rationale.
    intra_bridge_isolation: bool
    reach_internet: bool
    reachable_networks: list[str]
    # Admin/management plane split per service.
    services_access: bool
    admin_ui_access: bool
    ssh_access: bool
    # Tailnet exposure (Tailscale subnet routing).
    expose_to_tailnet: bool
    # Per-network Tor routing. See db/models.py NetworkRow for the
    # semantics of each field.
    tor_route_mode: TorRouteMode = "off"
    tor_dns_over_tor: bool = False
    tor_kill_switch: bool = False

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

    # ── isolation ───────────────────────────────────────────────
    intra_bridge_isolation: bool = Field(
        default=False,
        description=(
            "L2: ports of the same bridge are cloisonnés from each other. "
            "Rare ; most setups achieve isolation through separate bridges."
        ),
    )
    reach_internet: bool = Field(
        default=True,
        description=(
            "L3: clients of this network can route to the wan zone. "
            "Set False for purely-local segments (e.g. lab without internet)."
        ),
    )
    reachable_networks: list[str] = Field(
        default_factory=list,
        description=(
            "L3: explicit list of OTHER network slugs this one can route "
            "to besides wan. Empty = isolated from every other subnet. "
            "Example: ['lan'] = can reach main LAN, no other."
        ),
    )
    # ── admin/management plane (was a single `admin_access` flag) ──
    services_access: bool = Field(
        default=True,
        description=(
            "Input policy for essential services : DHCP, DNS local "
            "(dnsmasq), ICMP. False = clients can't get an IP or "
            "resolve names through the Slate ; strongly discouraged "
            "unless you really know why."
        ),
    )
    admin_ui_access: bool = Field(
        default=False,
        description=(
            "Input policy for the admin web UI (LuCI + GL.iNet, "
            "TCP 80 & 443). Default OFF — only trusted networks "
            "should be able to manage the Slate."
        ),
    )
    ssh_access: bool = Field(
        default=False,
        description=(
            "Input policy for SSH / dropbear (TCP 22). Default OFF — "
            "explicit opt-in per network."
        ),
    )

    # ── tailnet exposure ────────────────────────────────────────
    expose_to_tailnet: bool = Field(
        default=False,
        description=(
            "Advertise this network's CIDR as a subnet route on the "
            "tailnet (Tailscale `--advertise-routes`). False = the "
            "network stays invisible to remote tailnet peers ; True = "
            "any tailnet peer can reach hosts in this subnet via the "
            "Slate's tailscale0 interface (useful for e.g. remote Plex "
            "access from a phone)."
        ),
    )

    # ── per-network Tor routing ─────────────────────────────────
    tor_route_mode: TorRouteMode = Field(
        default="off",
        description=(
            "Tor integration for this network. "
            "`off` (default) ignores Tor entirely. "
            "`transparent` NATs every WAN-bound connection through the "
            "Slate's Tor daemon — high latency, capped throughput, but "
            "fully automatic for connected clients (use this for OSINT "
            "networks). "
            "`socks_only` does not redirect, only exposes the Tor SOCKS5 "
            "proxy on the gateway IP — clients opt in per app."
        ),
    )
    tor_dns_over_tor: bool = Field(
        default=False,
        description=(
            "When `tor_route_mode=transparent`, also redirect this "
            "network's DNS queries to Tor's DNSPort. Avoids DNS leaks to "
            "the upstream resolver. Ignored otherwise."
        ),
    )
    tor_kill_switch: bool = Field(
        default=False,
        description=(
            "When `tor_route_mode=transparent`, if the Tor daemon is "
            "down DROP this network's WAN egress (fail-closed). Default "
            "OFF = fail-open (clients keep regular internet, lose Tor). "
            "Turn ON when leakage is unacceptable."
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
