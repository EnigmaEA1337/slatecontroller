"""Pydantic models for the Tor subsystem (global daemon + bridges + status).

Per-network routing toggles live on :class:`app.networks.models.NetworkPublic`
— this module owns only the cross-cutting bits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TorBridgeKind = Literal["obfs4", "webtunnel", "snowflake", "vanilla"]


class TorSettings(BaseModel):
    """Global Tor daemon settings (singleton)."""

    daemon_enabled: bool = False
    use_bridges: bool = False
    # ISO-3166-1 alpha-2 country code (lowercase) the user wants every
    # circuit to exit from, e.g. "ch", "de", "se". Empty = let Tor pick.
    # When set, the handler emits `ExitNodes {xx}` + `StrictNodes 1` in
    # torrc — circuits that can't satisfy the constraint will fail rather
    # than silently picking another country.
    exit_country_code: str = ""
    updated_at: datetime | None = None


class TorSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daemon_enabled: bool = False
    use_bridges: bool = False
    exit_country_code: str = Field(
        default="", max_length=2, pattern=r"^[a-z]{0,2}$",
    )


class TorBridge(BaseModel):
    """A single Tor bridge line as displayed by the API."""

    id: int
    kind: TorBridgeKind = "obfs4"
    bridge_line: str
    note: str = ""
    enabled: bool = True
    created_at: datetime


class TorBridgeWrite(BaseModel):
    """Create / update body — `id` and `created_at` are server-assigned."""

    model_config = ConfigDict(extra="forbid")

    kind: TorBridgeKind = "obfs4"
    bridge_line: str = Field(min_length=8, max_length=512)
    note: str = Field(default="", max_length=128)
    enabled: bool = True


# ── Live status (queried from the device, not stored) ────────────────


class TorInstallStatus(BaseModel):
    """Which Tor-related packages are present on the device."""

    tor: bool = False
    tor_geoipdb: bool = False
    obfs4proxy: bool = False


class TorRelayHop(BaseModel):
    """One hop in a Tor circuit (entry / middle / exit)."""

    fingerprint: str
    nickname: str
    ip: str | None = None
    # ISO-3166-1 alpha-2 (lowercase). None when the GeoIP file doesn't
    # know this IP (rare; should only happen if tor-geoipdb isn't
    # installed).
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    bandwidth_kbps: int | None = None


class TorCircuitInfo(BaseModel):
    """One row of the device's currently-built circuits, when available."""

    circuit_id: str
    purpose: str = ""
    build_flags: list[str] = Field(default_factory=list)
    hops: list[TorRelayHop] = Field(default_factory=list)

    @property
    def exit_country(self) -> str | None:
        if self.hops:
            return self.hops[-1].country
        return None


class TorStatus(BaseModel):
    """Live snapshot of the on-device Tor daemon. None fields = unknown.

    Filled by `app.tor.client.fetch_status()` over SSH. Cheap to call (a few
    `ss`/`tor` shell commands + a control-port query if reachable).
    """

    install: TorInstallStatus
    daemon_running: bool = False
    control_port_reachable: bool = False
    bootstrap_progress: int | None = None         # 0-100, None if unknown
    bootstrap_phase: str | None = None            # "Done" or e.g. "Connecting to a relay"
    socks_port: int | None = None                 # default 9050
    trans_port: int | None = None                 # only when transparent routing is on
    dns_port: int | None = None
    exit_ip: str | None = None                    # current external IP (Tor exit)
    exit_country: str | None = None               # ISO country of exit_ip
    circuits: list[TorCircuitInfo] = Field(default_factory=list)
    uptime_seconds: int | None = None
    # Traffic counters since the daemon started (bytes).
    bytes_read: int | None = None
    bytes_written: int | None = None
    last_probe_at: datetime | None = None
