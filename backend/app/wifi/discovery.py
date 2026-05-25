"""Discover live Wi-Fi SSID definitions from a Slate.

Parses ``uci show wireless`` from the device, extracts each
``config wifi-iface`` section, and infers band + security from the
MediaTek mt76 driver's iface-naming convention (`ra*` = 2.4 GHz,
`rai*` = 5 GHz, `raix*` = 6 GHz) and OpenWrt's `encryption` field.

The output is just a list of dataclasses ; mapping them into the
controller's `WifiSsidStore` (with slug normalisation, skip-on-collision
etc.) lives in the route handler so this module stays I/O-free apart
from the single SSH call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.slate.ssh import SlateSSH, SlateSSHError
from app.wifi.models import WifiBand, WifiSecurity


@dataclass(frozen=True)
class DiscoveredSsid:
    """One on-Slate SSID, ready to be turned into a WifiSsidCreate."""

    iface: str          # uci section name, e.g. "ra0"
    ssid_name: str      # broadcast name, e.g. "GL-BE10000-759"
    band: WifiBand
    security: WifiSecurity
    disabled: bool
    network: str        # uci `network` attribute, e.g. "lan", "guest"


# mt76 driver convention (verified live on the Slate 7 Pro / MT7990 chip).
# Order matters : "raix" must be tested before "rai" before "ra" since each
# is a prefix of the previous. The values are the canonical literals from
# WifiBand.
_BAND_BY_IFACE_PREFIX: tuple[tuple[str, WifiBand], ...] = (
    ("raix", "6GHz"),
    ("rai", "5GHz"),
    ("ra", "2GHz"),
)

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
    return "5GHz"  # safe default — most modern home APs default to 5G


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

    out: list[DiscoveredSsid] = []
    for iface, attrs in sections.items():
        if attrs.get("_type") != "wifi-iface":
            continue  # radios + other sections aren't broadcasts
        ssid = (attrs.get("ssid") or "").strip()
        if not ssid:
            continue  # hidden / un-broadcast
        mode = (attrs.get("mode") or "ap").strip().lower()
        if mode != "ap":
            continue  # only AP-mode interfaces are SSIDs the user broadcasts
        out.append(
            DiscoveredSsid(
                iface=iface,
                ssid_name=ssid,
                band=_band_for(iface),
                security=_security_for(attrs.get("encryption")),
                disabled=attrs.get("disabled") == "1",
                network=(attrs.get("network") or "lan").strip(),
            )
        )
    return out
