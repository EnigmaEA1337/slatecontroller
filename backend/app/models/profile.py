"""Pydantic models for contextual profiles.

A profile bundles the desired state of every subsystem (VPN, Tor, Tailscale,
AdGuard, SSIDs, DNS, firewall, logging) so that activating it reconfigures
the Slate atomically. The YAML schema is intentionally loose so that
non-engineers can edit profiles by hand; validation lives here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VpnType = Literal["wireguard", "openvpn", "none"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class VPNConfig(BaseModel):
    """VPN client configuration for the profile."""

    type: VpnType = "none"
    client: str | None = Field(
        default=None,
        description="Name of the configured client (matches the GL.iNet config name).",
    )
    kill_switch: bool = False


class TorConfig(BaseModel):
    enabled: bool = False
    bridge: bool = False


class TailscaleConnectionOverride(BaseModel):
    """Optional per-profile overrides for the `tailscale set` runtime flags.

    Each field is None by default = "inherit from the last-applied config"
    (don't touch it on activation). Set to a concrete value to override it
    when this profile activates.

    NB: ``advertise_routes`` is **NOT** here. Subnet routing on the
    tailnet is a network property (``Network.expose_to_tailnet``), not a
    profile property — a subnet is either reachable from the tailnet or
    it isn't, regardless of which profile is active. The sync layer
    computes `--advertise-routes` from the network catalog and ignores
    any legacy value carried by old profile payloads.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_advertise_routes(cls, data: object) -> object:
        """Silently drop legacy `advertise_routes` from old payloads.

        The field was retired (routing is per-network now — see class
        docstring). Old DB rows still carry it ; we strip it on load
        so `extra="forbid"` doesn't fail validation. Typos in any
        other field continue to fail loudly.
        """
        if isinstance(data, dict):
            data.pop("advertise_routes", None)
        return data

    accept_routes: bool | None = None
    accept_dns: bool | None = None
    advertise_exit_node: bool | None = None
    # Empty string is a valid value: it explicitly DISABLES exit-node usage
    # (drops 0.0.0.0/0 from tailscale0). None = leave the current setting.
    exit_node: str | None = None
    shields_up: bool | None = None


class TailscaleHAOverride(BaseModel):
    """Optional per-profile overrides for the HA watchdog config.

    Same None = inherit semantics as TailscaleConnectionOverride. Setting
    `enabled=true` plus a candidates list will turn the watchdog on for
    this profile without affecting other profiles.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    candidates: list[str] | None = None
    failsafe_mode: Literal["fail_open", "keep"] | None = None


class TailscaleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    admin_only: bool = Field(
        default=False,
        description="If True, only the admin Tailnet device can reach this Slate.",
    )
    connection: TailscaleConnectionOverride | None = Field(
        default=None,
        description="Optional `tailscale set` overrides applied when this profile activates.",
    )
    ha: TailscaleHAOverride | None = Field(
        default=None,
        description="Optional HA exit-node watchdog config applied with this profile.",
    )


class ProfileSSIDRef(BaseModel):
    """Per-profile decision: which SSIDs are ON/OFF and which network they
    bind to in THIS profile.

    The SSID definition (broadcast name, bands, security, PSK) is pure
    layer-2 and lives in the wifi_ssids catalog, referenced by `slug`.
    The L2→L3 binding (which bridge/subnet the SSID routes to) is a
    profile concern — the same SSID can map to different networks
    depending on the active profile, exactly like a physical switch
    port. Hence `network_slug` lives here, not on the catalog SSID.
    """

    slug: str = Field(min_length=1, description="References WifiSsid.slug.")
    enabled: bool = False
    network_slug: str = Field(
        default="lan",
        min_length=1,
        max_length=64,
        description=(
            "Which Network (bridge/subnet) this SSID binds to when this "
            "profile is active. References Network.slug."
        ),
    )


class FirewallConfig(BaseModel):
    lockdown: bool = False
    geoip_whitelist: list[str] = Field(default_factory=list)
    block_telemetry: bool = False
    block_all_outbound: bool = Field(
        default=False,
        description="Lockdown profile: only explicit whitelist outbound is allowed.",
    )


class LoggingConfig(BaseModel):
    level: LogLevel = "INFO"
    forward_to_siem: bool = False


# Keys silently dropped from stored payloads — moved out of the per-profile
# shape into either a cross-cutting setting or per-network state. Existing
# YAML / stored profiles with these blocks load cleanly ; new profiles
# never write them. See _drop_legacy_keys for the why per key.
_LEGACY_KEYS = {"dns", "adguard", "tor"}


class Profile(BaseModel):
    """A contextual profile — one YAML file, one Profile."""

    # Stay strict: typos in YAML keys should fail fast rather than silently drop.
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_keys(cls, data: object) -> object:
        """Drop fields that were intentionally removed from the schema.

        Lets us keep `extra="forbid"` (typo protection) while still loading
        old YAML / DB rows that include now-deprecated blocks. Keys listed
        in `_LEGACY_KEYS` are silently popped; anything else still raises.
        Today's deprecated blocks :
          - ``dns``     : moved to per-network DNS protection (Networks
                          page) — see [[dns/manager]].
          - ``adguard`` : the per-profile global toggle / blocklists are
                          fully subsumed by per-network DNS protection
                          (each ``NetworkDnsProtectionRow`` drives an
                          AdGuard persistent-client with its own
                          filtering + blocklists). Keeping it would
                          create config ambiguity (profile says OFF but
                          a network says paranoid → which wins ?). So
                          we silently drop it on load and the AdGuard
                          daemon itself is implicitly always on.
          - ``tor``     : same idea. The daemon master switch + bridges
                          + exit_country live on ``TorSettings`` (DB,
                          Réseau → Tor page) ; routing is decided
                          per-network via ``NetworkRow.tor_route_mode``.
                          A per-profile ``tor.enabled`` would conflict
                          with both — silently dropped.
        """
        if isinstance(data, dict):
            for key in _LEGACY_KEYS:
                data.pop(key, None)
        return data

    name: str = Field(min_length=1, description="Unique identifier, used in URLs.")
    description: str = ""
    icon: str | None = Field(default=None, description="Lucide icon name (frontend hint).")
    color: str | None = Field(default=None, description="Hex color, e.g. '#3b82f6'.")

    vpn: VPNConfig = Field(default_factory=VPNConfig)
    tailscale: TailscaleConfig = Field(default_factory=TailscaleConfig)
    # NB: per-profile AdGuard + Tor blocks were removed. AdGuard runs
    # always-on and its filtering is per-network ; Tor's daemon switch +
    # bridges + exit_country live on `TorSettings` (global) and its
    # routing decisions on `NetworkRow.tor_*` (per-network). Legacy
    # `adguard:` / `tor:` blocks in stored payloads are silently dropped
    # by `_drop_legacy_keys` above.
    ssids: list[ProfileSSIDRef] = Field(default_factory=list)
    # NB: DNS protection is no longer a per-profile concern — it lives on
    # the network itself (Networks > DNS protection widget). The legacy
    # `dns: {servers, forced}` block has been removed; YAML loaders silently
    # drop the key thanks to BaseModel's default extra='ignore'.
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
