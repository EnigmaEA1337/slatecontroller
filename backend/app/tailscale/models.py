"""Pydantic models for Tailscale state + config."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Tailscale's BackendState as exposed by `tailscale status --json`.
BackendState = Literal[
    "NoState",       # daemon hasn't started yet
    "NeedsLogin",    # auth required
    "NeedsMachineAuth",  # admin approval pending
    "Stopped",       # explicitly down
    "Starting",
    "Running",
]


class TailscalePeer(BaseModel):
    """One peer in the tailnet (subset of fields we care about)."""

    hostname: str
    dns_name: str = ""
    tailscale_ips: list[str] = Field(default_factory=list)
    online: bool = False
    os: str = ""
    user: str = ""
    last_seen: datetime | None = None
    # Routes this peer advertises (we may want to accept these).
    primary_routes: list[str] = Field(default_factory=list)
    # Is this peer acting as exit node?
    exit_node: bool = False
    exit_node_option: bool = False  # peer offers exit-node


class TailscaleStatus(BaseModel):
    """Snapshot of `tailscale status --json` reduced to what the UI needs."""

    installed: bool = True             # binary present (we always check)
    daemon_running: bool = False       # tailscaled process alive
    backend_state: BackendState = "NoState"
    auth_url: str | None = None        # login URL when NeedsLogin
    # Self node info
    hostname: str = ""
    tailscale_ips: list[str] = Field(default_factory=list)
    tailnet: str = ""                  # like "tail-scales.ts.net"
    self_id: str = ""
    # Config currently active
    accept_routes: bool = False
    advertised_routes: list[str] = Field(default_factory=list)
    exit_node_enabled: bool = False    # this Slate offers itself as exit-node
    use_exit_node: str = ""            # hostname of remote exit node in use
    # Peers
    peers: list[TailscalePeer] = Field(default_factory=list)
    # Plain stderr if the CLI returned an error we should surface.
    error: str = ""


class TailscaleConfigInput(BaseModel):
    """User-supplied config when bringing Tailscale up."""

    auth_key: str | None = None       # optional: re-use stored one if absent
    hostname: str | None = None       # override device name in admin UI
    accept_routes: bool = True
    accept_dns: bool = False          # we run AdGuard locally, default off
    advertise_routes: list[str] = Field(default_factory=list)
    # Set True to make THIS Slate act as exit-node for others on the tailnet.
    advertise_exit_node: bool = False
    # Use ANOTHER peer as exit node (hostname). Empty = no exit node.
    exit_node: str = ""
    # Tailscale --shields-up blocks all incoming from peers.
    shields_up: bool = False


class TailscaleConnectResponse(BaseModel):
    """Result of POST /api/tailscale/connect."""

    success: bool
    status: TailscaleStatus
    note: str = ""
    auth_url: str | None = None       # if interactive auth needed
