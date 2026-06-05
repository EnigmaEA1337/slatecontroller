"""Pydantic models for the Wi-Fi SSID catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Discrete radios. "2" is shorthand for 2.4 GHz — we drop the "GHz"
# suffix from the wire format because we store bands as a list and
# compact tokens make the JSON terser. MLO (Wi-Fi 7 Multi-Link) is
# expressed via the separate ``mlo`` boolean, not a band value, so a
# Wi-Fi-7 SSID still declares which bands it groups.
WifiBand = Literal["2", "5", "6"]
WifiSecurity = Literal["WPA3-SAE", "WPA3-PSK", "WPA2-PSK", "WPA2-WPA3-Mixed", "open"]
# Protected Management Frames (802.11w) policy. "required" enforces PMF
# and refuses legacy clients ; "optional" advertises PMF capability and
# lets clients decide ; "disabled" turns it off entirely. WPA3 mandates
# at least optional, and "required" should be the default for WPA3-only
# deployments.
WifiPMF = Literal["disabled", "optional", "required"]


class WifiSsidAdvanced(BaseModel):
    """MTK-specific knobs exposed under the "Avancé" section of the UI.

    Defaults are conservative : the same behaviour you'd get without
    explicitly setting these on UCI. The operator opts INTO each
    tradeoff (PMF=required blocks WPA2 clients, FT/RRM/BTM may break
    poorly-behaved roamers, higher DTIM trades multicast latency for
    client power savings, …).
    """

    pmf: WifiPMF = "optional"
    ft_802_11r: bool = False
    rrm_802_11k: bool = False
    btm_802_11v: bool = False
    dtim_period: int = Field(default=2, ge=1, le=10)
    wmm: bool = True
    proxy_arp: bool = False
    wds: bool = False


class WifiSsidPublic(BaseModel):
    """SSID record exposed via the API — password never included."""

    slug: str
    ssid_name: str
    bands: list[WifiBand]
    mlo: bool
    security: WifiSecurity
    # NB: no network_slug — SSID is pure L2, network binding is per-profile.
    client_isolation: bool
    hidden: bool
    notes: str
    has_password: bool
    advanced: WifiSsidAdvanced = Field(default_factory=WifiSsidAdvanced)
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
    bands: list[WifiBand] = Field(
        default_factory=lambda: ["5"],
        min_length=1,
        description=(
            "Bands this SSID is broadcast on. The agent creates one "
            "wifi-iface per band, all sharing ssid + key. Order is "
            "irrelevant ; duplicates are stripped."
        ),
    )
    mlo: bool = Field(
        default=False,
        description=(
            "Wi-Fi 7 Multi-Link Operation. When True, the agent bundles "
            "the bands under a single MLD instead of N independent "
            "VAPs ; only Wi-Fi 7 clients see the speedup."
        ),
    )
    security: WifiSecurity = "WPA3-SAE"
    password: str | None = Field(default=None, max_length=128)
    client_isolation: bool = Field(
        default=False,
        description="If True, clients within this SSID cannot talk to each other.",
    )
    hidden: bool = Field(
        default=False,
        description=(
            "If True, the AP omits the SSID from beacon frames "
            "(UCI `hidden=1`). Not a security control — clients still "
            "leak the name in probe requests. Mostly cosmetic."
        ),
    )
    notes: str = Field(default="", max_length=256)
    # MTK advanced section. The default is the conservative all-fields
    # snapshot ; the UI's "Avancé" pane lets the operator override.
    advanced: WifiSsidAdvanced = Field(default_factory=WifiSsidAdvanced)

    @field_validator("bands")
    @classmethod
    def _dedupe_bands(cls, value: list[str]) -> list[str]:
        # Preserve the canonical order ["2", "5", "6"] so the wire shape
        # is deterministic ; drop duplicates the user may have sent.
        order = {"2": 0, "5": 1, "6": 2}
        seen: set[str] = set()
        out: list[str] = []
        for b in value:
            if b in seen:
                continue
            seen.add(b)
            out.append(b)
        out.sort(key=lambda b: order.get(b, 99))
        return out  # type: ignore[return-value]


class WifiSsidCreate(WifiSsidWrite):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
