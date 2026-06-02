"""Discover live Wi-Fi SSID definitions from a Slate.

Parses ``uci show wireless`` from the device, extracts each
``config wifi-iface`` section, and infers band + security from the
MediaTek mt76 driver's iface-naming convention (`ra*` = 2.4 GHz,
`rai*` = 5 GHz, `rax*` = 6 GHz) and OpenWrt's `encryption` field.

The output is just a list of dataclasses ; mapping them into the
controller's `WifiSsidStore` (with slug normalisation, skip-on-collision
etc.) lives in the route handler so this module stays I/O-free apart
from the single SSH call.

Multi-band : when the same SSID broadcast name appears on more than one
iface (e.g. a dual-band AP with the same name on rai0 and ra0), we emit
**one** ``DiscoveredSsid`` per unique ssid_name with the band tokens
merged into ``bands``. That mirrors what the controller now stores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.slate.ssh import SlateSSH, SlateSSHError
from app.wifi.models import WifiBand, WifiSecurity


@dataclass(frozen=True)
class DiscoveredSsid:
    """One on-Slate SSID, ready to be turned into a WifiSsidCreate."""

    # Section name on UCI side — for diagnostics only. When the SSID is
    # multi-band we keep the first iface we saw ; the others appear in
    # ``ifaces`` below.
    iface: str
    ifaces: tuple[str, ...]
    ssid_name: str
    bands: tuple[WifiBand, ...]
    security: WifiSecurity
    disabled: bool
    hidden: bool
    network: str        # uci `network` attribute, e.g. "lan", "guest"


# mt76 driver convention (verified live on the Slate 7 Pro / MT7990 chip).
# Order matters : "rax" must be tested before "rai" before "ra" since each
# is a prefix of the previous. The values are the canonical literals from
# WifiBand (compact tokens : "2" / "5" / "6").
_BAND_BY_IFACE_PREFIX: tuple[tuple[str, WifiBand], ...] = (
    ("rax", "6"),
    ("rai", "5"),
    ("ra", "2"),
)

# Canonical band order so multi-band records always come out sorted
# the same way regardless of UCI section ordering.
_BAND_ORDER: dict[WifiBand, int] = {"2": 0, "5": 1, "6": 2}

# OpenWrt's `encryption` values → our `WifiSecurity` literals. Anything
# unknown defaults to WPA2-PSK which is the safest fallback that's still
# usable by every modern client. Open networks are explicit ("none", "owe").
_ENCRYPTION_MAP: dict[str, WifiSecurity] = {
    "psk2+ccmp": "WPA2-PSK",
    "psk2+tkip+ccmp": "WPA2-PSK",
    "psk2": "WPA2-PSK",
    "psk-mixed+ccmp": "WPA2-PSK",
    "psk-mixed": "WPA2-PSK",
    "sae": "WPA3-SAE",
    "sae-mixed": "WPA2-WPA3-Mixed",
    "psk3": "WPA3-SAE",
    "none": "open",
    "owe": "open",
}


def _band_for(iface: str) -> WifiBand:
    for prefix, band in _BAND_BY_IFACE_PREFIX:
        if iface.startswith(prefix):
            return band
    return "5"  # safe default — most modern home APs default to 5 GHz


def _security_for(encryption: str | None) -> WifiSecurity:
    if not encryption:
        return "WPA2-PSK"
    return _ENCRYPTION_MAP.get(encryption.strip().lower(), "WPA2-PSK")


def slugify_ssid_name(name: str) -> str:
    """Turn a broadcast name into a UCI-safe lowercase slug.

    Examples :
      ``GL-BE10000-759``       → ``gl_be10000_759``
      ``Mon Réseau Wi-Fi``     → ``mon_reseau_wi_fi``
      ``🍿 GhostLine``         → ``ghostline``
    """
    # Normalize accents to ASCII best-effort, drop non-printable.
    import unicodedata
    norm = unicodedata.normalize("NFKD", name)
    ascii_ = norm.encode("ascii", errors="ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", ascii_).strip("_").lower()
    return s or "ssid"


async def discover_wireless(ssh: SlateSSH) -> list[DiscoveredSsid]:
    """Return one record per AP-mode SSID currently in the Slate's
    `wireless` UCI config (whether enabled or not).

    Filters :
      - skip non-AP sections (client/repeater stations live in the same
        file but aren't broadcast SSIDs we'd want to manage)
      - skip sections without a `ssid` field (radios, not interfaces)

    Multi-band coalescing : sections sharing the same ``ssid`` value are
    merged into a single record with ``bands`` listing every band found.
    Security/network/disabled are taken from the first section we see ;
    if siblings disagree (rare misconfig) we still surface one record so
    the user can fix it from the UI.
    """
    try:
        r = await ssh.run("uci show wireless 2>/dev/null", timeout=10)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH uci show wireless failed: {exc}") from exc

    # Parse the flat `uci show` output into `{section: {attr: value}}`.
    # Lines come in two shapes :
    #   wireless.IFACE=TYPE                  (section declaration)
    #   wireless.IFACE.ATTR='value'          (option)
    sections: dict[str, dict[str, str]] = {}
    for raw in r.stdout.splitlines():
        line = raw.strip()
        # Option line first (more common).
        m = re.match(r"^wireless\.([^.=]+)\.([^=]+)=(.*)$", line)
        if m:
            iface, attr, value = m.group(1), m.group(2), m.group(3)
            if value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            sections.setdefault(iface, {})[attr] = value
            continue
        # Section declaration : wireless.X=wifi-iface
        m = re.match(r"^wireless\.([^.=]+)=([A-Za-z][A-Za-z0-9_-]*)$", line)
        if m:
            iface, typ = m.group(1), m.group(2)
            sections.setdefault(iface, {})["_type"] = typ

    # Group by ssid_name so multi-band same-name SSIDs collapse to one.
    grouped: dict[str, dict] = {}
    for iface, attrs in sections.items():
        if attrs.get("_type") != "wifi-iface":
            continue
        ssid = (attrs.get("ssid") or "").strip()
        if not ssid:
            continue
        mode = (attrs.get("mode") or "ap").strip().lower()
        if mode != "ap":
            continue
        band = _band_for(iface)
        bucket = grouped.setdefault(
            ssid,
            {
                "first_iface": iface,
                "ifaces": [],
                "bands": set(),
                "security": _security_for(attrs.get("encryption")),
                "disabled": attrs.get("disabled") == "1",
                "hidden": attrs.get("hidden") == "1",
                "network": (attrs.get("network") or "lan").strip(),
            },
        )
        bucket["ifaces"].append(iface)
        bucket["bands"].add(band)

    out: list[DiscoveredSsid] = []
    for ssid_name, b in grouped.items():
        ordered_bands: list[WifiBand] = sorted(
            b["bands"], key=lambda x: _BAND_ORDER.get(x, 99),
        )
        out.append(
            DiscoveredSsid(
                iface=b["first_iface"],
                ifaces=tuple(b["ifaces"]),
                ssid_name=ssid_name,
                bands=tuple(ordered_bands),
                security=b["security"],
                disabled=b["disabled"],
                hidden=b["hidden"],
                network=b["network"],
            )
        )
    return out
