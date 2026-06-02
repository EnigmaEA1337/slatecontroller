"""Live state of the Slate's WiFi slots.

Different intent from `discovery.py` (which feeds the SSID catalog from
the Slate) : this module gives the operator a *diagnostic* view of every
UCI wireless section + its real broadcast status. Used by the WiFi UI's
"État live côté Slate" panel to spot drift between what the controller
*thinks* is configured and what the Slate actually broadcasts.

Cross-references two SSH probes :

  - ``uci show wireless`` : the persisted config (slots, disabled flag,
    bound network, SSID name, security, …)
  - ``iwinfo``            : the runtime AP-mode view (which ifaces are
    in Master mode + what ESSID they actually broadcast)

Each slot we discover is classified :

  - **slate_managed**  : section name starts with ``SC_WL_`` (allocated
    by the wifi.sh handler)
  - **glinet_stock**   : section name is a GL.iNet factory iface
    (``ra0``, ``rai0``, ``rax0``, ``guest2g``, ``guest5g`` …)
  - **mlo_link**       : section is a per-band link of an MLD group
    (``wlanmld*``) — the MLD group is the actual broadcaster
  - **other**          : anything else (rare)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.slate.ssh import SlateSSH, SlateSSHError
from app.wifi.models import WifiBand


# Reuse the iface-prefix → band mapping from discovery. We keep the
# fallback "5" for unknown prefixes (MLO mld0/mld1 sections, virtual
# loopbacks) — caller can override via the explicit ``bands_csv`` from
# UCI when available.
_BAND_BY_IFACE_PREFIX: tuple[tuple[str, WifiBand], ...] = (
    ("rax", "6"),
    ("rai", "5"),
    ("ra", "2"),
    ("wlanmld6g", "6"),
    ("wlanmld5g", "5"),
    ("wlanmld2g", "2"),
    ("wlanmldguest6g", "6"),
    ("wlanmldguest5g", "5"),
    ("wlanmldguest2g", "2"),
    ("guest6g", "6"),
    ("guest5g", "5"),
    ("guest2g", "2"),
)


def _band_for_iface(name: str) -> WifiBand | None:
    for prefix, band in _BAND_BY_IFACE_PREFIX:
        if name.startswith(prefix):
            return band
    return None


def _slot_kind(section_name: str) -> str:
    """Classify the slot into one of slate_managed / glinet_stock / mlo_link / other."""
    if section_name.startswith("SC_WL_"):
        return "slate_managed"
    if section_name.startswith("wlanmld"):
        return "mlo_link"
    if section_name in {
        "ra0", "ra1", "rai0", "rai1", "rax0", "rax1",
        "guest2g", "guest5g", "guest6g",
        "mld0", "mld1",
    }:
        return "glinet_stock"
    return "other"


@dataclass
class WifiSlotState:
    """One UCI wireless section's diagnostic view."""

    section_name: str         # e.g. "rai0", "SC_WL_NEXUS7_5", "wlanmld5g"
    ifname: str               # the kernel iface name (often same as section)
    band: WifiBand | None     # 2/5/6, None for radios or unidentifiable
    mode: str                 # "ap" / "sta" / "mesh" / "monitor" / "unknown"
    ssid_uci: str             # what /etc/config/wireless says
    ssid_broadcast: str | None  # what iwinfo Master-mode reports, or None
    enabled: bool             # uci disabled=0
    network: str              # bound network (lan / guest / blackwall / …)
    encryption: str           # uci encryption attribute
    is_up: bool               # broadcasting (Master mode + ssid_broadcast set)
    slot_kind: str            # slate_managed / glinet_stock / mlo_link / other
    marker: bool = False      # has the _WIFI_MARK we use to claim slots
    notes: list[str] = field(default_factory=list)


# Regex to pull `key='value'` (or `key=value`) out of one `uci show` line.
_UCI_LINE = re.compile(
    r"^wireless\.(?P<sec>[A-Za-z0-9_]+)\.(?P<key>[a-z_]+)='?(?P<val>[^']*)'?$",
)
# Regex for iwinfo ESSID lines. Format example :
#   rai0      ESSID: "🐦‍🔥 BLACK_ICE"
_IWINFO_ESSID = re.compile(r"^(?P<ifn>[a-z0-9]+)\s+ESSID:\s*\"(?P<ssid>.*)\"$")
# Same for "ESSID: unknown" (no quotes) — iface exists but not broadcasting.
_IWINFO_UNKNOWN = re.compile(r"^(?P<ifn>[a-z0-9]+)\s+ESSID:\s*unknown\s*$")
_IWINFO_MODE = re.compile(r"^\s*Mode:\s*(?P<mode>\w+)")


def _parse_uci_show(raw: str) -> dict[str, dict[str, str]]:
    """Group uci show output by section name → field dict."""
    out: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        m = _UCI_LINE.match(line.strip())
        if not m:
            continue
        sec = m.group("sec")
        key = m.group("key")
        val = m.group("val")
        out.setdefault(sec, {})[key] = val
    return out


def _parse_iwinfo(raw: str) -> dict[str, tuple[str | None, str]]:
    """Return ``{ifname: (broadcast_ssid_or_None, mode)}`` from iwinfo output.

    Mode is the iwinfo "Mode:" line right after the iface header — we
    walk the file linearly to associate each iface with its mode.
    """
    result: dict[str, tuple[str | None, str]] = {}
    current_iface: str | None = None
    current_ssid: str | None = None
    for line in raw.splitlines():
        # New iface ?
        m_essid = _IWINFO_ESSID.match(line)
        if m_essid:
            current_iface = m_essid.group("ifn")
            current_ssid = m_essid.group("ssid")
            continue
        m_unknown = _IWINFO_UNKNOWN.match(line)
        if m_unknown:
            current_iface = m_unknown.group("ifn")
            current_ssid = None
            continue
        if current_iface is None:
            continue
        m_mode = _IWINFO_MODE.match(line)
        if m_mode:
            mode = m_mode.group("mode").lower()
            mode_short = {
                "master": "ap",
                "client": "sta",
                "mesh": "mesh",
                "monitor": "monitor",
                "ad-hoc": "ibss",
            }.get(mode, mode)
            result[current_iface] = (current_ssid, mode_short)
            # iwinfo usually has one Mode line per iface ; reset to avoid
            # spillover into the next block.
            current_iface = None
            current_ssid = None
    return result


async def get_slate_wifi_state(ssh: SlateSSH) -> list[WifiSlotState]:
    """Probe the Slate, return one ``WifiSlotState`` per UCI section.

    Two SSH commands, run in sequence (small payloads). Sections without
    a ``ssid`` field are skipped — those are radios, not interfaces.
    """
    try:
        uci_raw = await ssh.run("uci show wireless 2>/dev/null", timeout=10)
        iw_raw = await ssh.run("iwinfo 2>/dev/null", timeout=10)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH probe failed: {exc}") from exc

    sections = _parse_uci_show(uci_raw.stdout)
    live = _parse_iwinfo(iw_raw.stdout)

    out: list[WifiSlotState] = []
    for sec_name, fields in sorted(sections.items()):
        # Radios have `type=device` ; interfaces have `type=device` too on
        # some firmwares but use `ssid` to differentiate. We require ssid.
        ssid_uci = fields.get("ssid", "")
        if not ssid_uci:
            continue
        ifname = fields.get("ifname") or sec_name
        broadcast, mode = live.get(ifname, (None, "unknown"))
        band = _band_for_iface(ifname) or _band_for_iface(sec_name)
        enabled = fields.get("disabled", "0") == "0"
        network = fields.get("network", "")
        encryption = fields.get("encryption", "")
        marker = fields.get("_slate_ctrl_managed") == "1"
        is_up = mode == "ap" and broadcast is not None and broadcast != ""

        notes: list[str] = []
        if broadcast and ssid_uci and broadcast != ssid_uci:
            notes.append(
                f"broadcast diverge de l'uci : « {broadcast} » vs « {ssid_uci} »"
            )
        if enabled and not is_up:
            notes.append("config enabled mais aucun broadcast — driver pas armed")
        if not enabled and is_up:
            notes.append("config disabled mais broadcast actif — driver pas re-armé")

        out.append(
            WifiSlotState(
                section_name=sec_name,
                ifname=ifname,
                band=band,
                mode=mode,
                ssid_uci=ssid_uci,
                ssid_broadcast=broadcast,
                enabled=enabled,
                network=network,
                encryption=encryption,
                is_up=is_up,
                slot_kind=_slot_kind(sec_name),
                marker=marker,
                notes=notes,
            )
        )
    return out
