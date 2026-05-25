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
    """

    model_config = ConfigDict(extra="forbid")

    accept_routes: bool | None = None
    accept_dns: bool | None = None
    advertise_routes: list[str] | None = None
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


class AdGuardConfig(BaseModel):
    enabled: bool = False
    lists: list[str] = Field(default_factory=list)


class ProfileSSIDRef(BaseModel):
    """Per-profile decision: which SSIDs from the central catalog are ON/OFF.

    The actual SSID definition (broadcast name, band, security, password) lives
    in the wifi_ssids catalog and is referenced by `slug`. A profile only says
    "I want SSID `mlo-main` enabled and `iot` disabled".
    """

    slug: str = Field(min_length=1, description="References WifiSsid.slug.")
    enabled: bool = False


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


_LEGACY_KEYS = {"dns"}


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
        Today: `dns` (moved to per-network DNS protection on the Networks
        page — see [[dns/manager]]).
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
    tor: TorConfig = Field(default_factory=TorConfig)
    tailscale: TailscaleConfig = Field(default_factory=TailscaleConfig)
    adguard: AdGuardConfig = Field(default_factory=AdGuardConfig)
    ssids: list[ProfileSSIDRef] = Field(default_factory=list)
    # NB: DNS protection is no longer a per-profile concern — it lives on
    # the network itself (Networks > DNS protection widget). The legacy
    # `dns: {servers, forced}` block has been removed; YAML loaders silently
    # drop the key thanks to BaseModel's default extra='ignore'.
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
