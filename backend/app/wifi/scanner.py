"""Channel scanner — RF survey of the air around the Slate.

Provides three layered capabilities :

  - **Neighbor AP enumeration** : runs ``iw dev <iface> scan`` over SSH,
    parses the BSS list into structured ``NeighborAP`` records (BSSID,
    SSID, channel, RSSI, security, capabilities).

  - **Channel scoring** : for each candidate channel of a band, computes
    a 0-100 score combining own-channel overlap (2.4 GHz uses fat 20/40
    channels that overlap), neighbor count, neighbor RSSI strength,
    DFS / radar history (5 GHz), and PSC-channel preference (6 GHz).
    Surfaces the best channel as a recommendation.

  - **Threat detection** : applies a small ruleset to the neighbor list
    to flag evil-twin candidates (foreign BSSID broadcasting one of OUR
    SSID names), legacy-crypto neighbors (WEP / WPA1), WPS-enabled APs
    (PixieDust surface), and abnormally-strong neighbors on the same
    channel (potential deauth source).

The scanner uses a slot-free design : it uses the EXISTING managed
slots' ifname for the scan (e.g. ra0/rai0/rax0 templates). The MTK
mt7990 driver lets a Master-mode iface trigger a passive/active scan
without bringing the BSS down, so this doesn't disrupt clients.
A future variant will be able to use a dedicated MONITOR slot for
continuous capture (Air Watch live mode) — that flow lives in
``wifi/monitor.py`` (TBD).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

from app.slate.ssh import SlateSSH, SlateSSHError
from app.wifi.models import WifiBand

# ---------------------------- band & channel maps ---------------------------- #

# Per-band candidate channels we score. Sourced from regdomain FR (EU):
#   2.4 GHz : 1..13 (channel 14 is JP-only)
#   5 GHz   : UNII-1 (36/40/44/48), UNII-2A (52/56/60/64 DFS),
#             UNII-2C (100..144 DFS), UNII-3 (149..165)
#   6 GHz   : PSC channels (5, 21, 37, 53, 69, 85, 101, 117, 133, 149,
#             165, 181, 197, 213, 229) — Wi-Fi 6E/7 devices scan only
#             those by default. Non-PSC are allowed but reach fewer
#             clients. We score PSC and non-PSC differently.
CHANNELS_24: tuple[int, ...] = tuple(range(1, 14))
CHANNELS_5_NON_DFS: frozenset[int] = frozenset({36, 40, 44, 48, 149, 153, 157, 161, 165})
CHANNELS_5_DFS: frozenset[int] = frozenset({
    52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
})
CHANNELS_5: tuple[int, ...] = tuple(sorted(CHANNELS_5_DFS | CHANNELS_5_NON_DFS))
CHANNELS_6_PSC: frozenset[int] = frozenset({
    5, 21, 37, 53, 69, 85, 101, 117, 133, 149, 165, 181, 197, 213, 229,
})
CHANNELS_6: tuple[int, ...] = tuple(sorted(CHANNELS_6_PSC))

# Non-overlapping channel groups on 2.4 GHz HT20. Used to score overlap.
NON_OVERLAP_24: tuple[frozenset[int], ...] = (
    frozenset({1, 2, 3, 4}),
    frozenset({5, 6, 7, 8, 9}),
    frozenset({10, 11, 12, 13}),
)


def _band_for_channel(ch: int) -> WifiBand:
    if 1 <= ch <= 14:
        return "2"
    if 36 <= ch <= 196:
        return "5"
    return "6"


# ---------------------------- structured outputs ---------------------------- #

ThreatLevel = Literal["info", "warn", "alert"]


@dataclass(frozen=True)
class NeighborAP:
    """One AP seen by the scan."""

    bssid: str
    ssid: str            # may be "" for hidden / cloaked
    hidden: bool
    channel: int
    band: WifiBand
    rssi_dbm: int        # negative, larger absolute = weaker
    security: str        # WPA3 / WPA2 / WPA / WEP / open / mixed
    ht_mode: str         # HT20 / HT40 / VHT80 / HE160 / EHT320 / ...
    is_wps_enabled: bool
    is_ours: bool = False  # set later if BSSID matches our own MAC list


@dataclass(frozen=True)
class ChannelScore:
    """How "good" a channel is for our broadcast."""

    band: WifiBand
    channel: int
    score: int                  # 0-100, higher = better
    neighbor_count: int         # APs we'd compete with on this channel
    is_dfs: bool                # 5 GHz radar-restricted ?
    is_psc: bool                # 6 GHz PSC ? (matters for client discovery)
    is_current: bool            # this channel is our currently-active one
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ThreatEvent:
    """One detection emitted by the threat-rules pass."""

    kind: str                   # evil_twin / legacy_crypto / wps_enabled / strong_neighbor
    level: ThreatLevel
    bssid: str
    ssid: str
    channel: int
    rssi_dbm: int
    message: str                # human-readable, FR


@dataclass(frozen=True)
class ScanResult:
    """Full output of a single band scan."""

    band: WifiBand
    iface: str
    started_at: float           # unix epoch, set by caller
    duration_s: float
    neighbors: list[NeighborAP]
    channel_scores: list[ChannelScore]
    recommended_channel: int | None
    current_channel: int | None
    threats: list[ThreatEvent]


# ---------------------------- iw scan parsing ---------------------------- #

# Each BSS block in `iw scan` output looks like :
#   BSS aa:bb:cc:11:22:33(on rai0)
#       freq: 5240
#       beacon interval: 100 TUs
#       capability: ESS Privacy (0x0411)
#       signal: -67.00 dBm
#       SSID: SomeNetwork
#       ...
_BSS_HEADER = re.compile(r"^BSS\s+([0-9a-fA-F:]{17})\b")
_FIELD = re.compile(r"^\s*([A-Za-z][^:]*):\s*(.*?)\s*$")


def _freq_to_channel(freq_mhz: int) -> int:
    """Map a center frequency in MHz to a channel number, all bands."""
    if 2412 <= freq_mhz <= 2484:
        if freq_mhz == 2484:
            return 14
        return (freq_mhz - 2407) // 5
    if 5000 <= freq_mhz < 6000:
        return (freq_mhz - 5000) // 5
    if 5950 <= freq_mhz <= 7115:
        return (freq_mhz - 5950) // 5
    return 0


def _security_from_caps(text: str) -> tuple[str, bool]:
    """Return (label, wps_enabled) from the IE/capability text dump of a BSS."""
    has_wps = "WPS" in text or "WPS:" in text
    if "RSN:" in text:
        # WPA2 or WPA3 — distinguish by AKM
        if "SAE" in text and "PSK" in text:
            return "WPA2-WPA3-Mixed", has_wps
        if "SAE" in text:
            return "WPA3-SAE", has_wps
        if "PSK" in text:
            return "WPA2-PSK", has_wps
        if "EAP" in text or "802.1x" in text:
            return "WPA2-EAP", has_wps
        return "WPA2", has_wps
    if "WPA:" in text:
        return "WPA1", has_wps
    if "Privacy" in text and "RSN" not in text and "WPA" not in text:
        return "WEP", has_wps
    return "open", has_wps


def _ht_mode_from_text(text: str) -> str:
    """Best-effort PHY mode from `iw scan` IE dump."""
    if "EHT capabilities" in text or "EHT operation" in text:
        if "320 MHz" in text:
            return "EHT320"
        if "160 MHz" in text:
            return "EHT160"
        if "80 MHz" in text:
            return "EHT80"
        return "EHT"
    if "HE capabilities" in text or "HE Operation" in text:
        if "160 MHz" in text:
            return "HE160"
        if "80 MHz" in text:
            return "HE80"
        return "HE"
    if "VHT capabilities" in text:
        if "160 MHz" in text:
            return "VHT160"
        if "80 MHz" in text:
            return "VHT80"
        return "VHT"
    if "HT capabilities" in text:
        if "HT40" in text or "40 MHz" in text:
            return "HT40"
        return "HT20"
    return "legacy"


def parse_iw_scan(raw: str) -> list[NeighborAP]:
    """Parse the text output of `iw dev <iface> scan`.

    iw's output is verbose and per-BSS multi-line. We split on the
    "BSS <mac>(...)" headers, accumulate each block's lines, then
    extract the salient fields.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in raw.splitlines():
        if _BSS_HEADER.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    out: list[NeighborAP] = []
    for block in blocks:
        header = block[0]
        m = _BSS_HEADER.match(header)
        if not m:
            continue
        bssid = m.group(1).lower()
        text = "\n".join(block)
        # Freq & channel
        ch = 0
        freq_match = re.search(r"freq:\s*(\d+)", text)
        if freq_match:
            ch = _freq_to_channel(int(freq_match.group(1)))
        # DS Parameter set also carries the channel (Wi-Fi 4/5 fallback)
        if ch == 0:
            ds = re.search(r"DS Parameter set: channel (\d+)", text)
            if ds:
                ch = int(ds.group(1))
        # Signal
        rssi = -100
        sig_match = re.search(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", text)
        if sig_match:
            rssi = int(float(sig_match.group(1)))
        # SSID (handle hidden)
        ssid = ""
        hidden = False
        ssid_match = re.search(r"^\s*SSID:\s*(.*)$", text, re.MULTILINE)
        if ssid_match:
            ssid = ssid_match.group(1).strip()
            if not ssid or all(b == "\\x00" for b in ssid.split()):
                hidden = True
                ssid = ""
        else:
            hidden = True
        # Security + WPS
        security, wps = _security_from_caps(text)
        ht_mode = _ht_mode_from_text(text)
        try:
            band = _band_for_channel(ch)
        except Exception:  # noqa: BLE001
            band = "5"
        out.append(NeighborAP(
            bssid=bssid,
            ssid=ssid,
            hidden=hidden,
            channel=ch,
            band=band,
            rssi_dbm=rssi,
            security=security,
            ht_mode=ht_mode,
            is_wps_enabled=wps,
        ))
    return out


# ---------------------------- scoring ---------------------------- #

def _rssi_to_weight(rssi_dbm: int) -> float:
    """Closer + stronger = bigger competitor. Maps -30 dBm → 1.0, -90 → 0.05.
    Used to weight a neighbor's "cost" against our score."""
    if rssi_dbm >= -30:
        return 1.0
    if rssi_dbm <= -90:
        return 0.05
    # linear interp between -30 and -90
    return max(0.05, min(1.0, (rssi_dbm + 90) / 60))


def _overlap_24(ch_a: int, ch_b: int) -> float:
    """How much two 2.4 GHz channels overlap (1.0 = identical, 0 = no overlap).
    20 MHz HT20 wide ; channels are 5 MHz apart. |Δch| < 4 → overlap."""
    delta = abs(ch_a - ch_b)
    if delta == 0:
        return 1.0
    if delta >= 5:
        return 0.0
    return (5 - delta) / 5  # linear : 1ch=0.8, 2ch=0.6, 3ch=0.4, 4ch=0.2


def score_channels(
    band: WifiBand,
    neighbors: list[NeighborAP],
    current_channel: int | None = None,
) -> list[ChannelScore]:
    """Return one ChannelScore per candidate channel on this band."""
    if band == "2":
        candidates = CHANNELS_24
    elif band == "5":
        candidates = CHANNELS_5
    else:
        candidates = CHANNELS_6
    out: list[ChannelScore] = []
    band_neighbors = [n for n in neighbors if n.band == band]
    for ch in candidates:
        reasons: list[str] = []
        # Base score is 100, deductions accumulate.
        score = 100.0
        # Count direct conflicts + adjacent overlap (2.4 GHz only).
        own_neighbors = 0
        overlap_cost = 0.0
        for n in band_neighbors:
            if band == "2":
                overlap = _overlap_24(ch, n.channel)
                if overlap > 0:
                    own_neighbors += 1 if overlap >= 0.4 else 0
                    overlap_cost += overlap * _rssi_to_weight(n.rssi_dbm) * 40
            else:
                # 5/6 GHz : channels don't overlap (different center freqs),
                # only direct match matters.
                if n.channel == ch:
                    own_neighbors += 1
                    overlap_cost += _rssi_to_weight(n.rssi_dbm) * 25
        score -= overlap_cost
        if overlap_cost > 0:
            reasons.append(f"{own_neighbors} AP voisin{'s' if own_neighbors > 1 else ''}")
        # DFS penalty on 5 GHz : not unusable, but risks radar eviction.
        is_dfs = band == "5" and ch in CHANNELS_5_DFS
        if is_dfs:
            score -= 15
            reasons.append("DFS (radar possible)")
        # PSC bonus on 6 GHz : clients only scan PSC by default.
        is_psc = band == "6" and ch in CHANNELS_6_PSC
        if band == "6" and not is_psc:
            score -= 25
            reasons.append("non-PSC (peu de clients scannent)")
        # 2.4 GHz : recommend 1/6/11 explicitly (no-overlap triad).
        if band == "2":
            if ch in {1, 6, 11}:
                score += 8
                reasons.append("canal recommandé (no-overlap)")
        is_current = current_channel is not None and current_channel == ch
        if is_current and score >= 60:
            reasons.append("ton canal actuel")
        out.append(ChannelScore(
            band=band,
            channel=ch,
            score=max(0, min(100, int(round(score)))),
            neighbor_count=own_neighbors,
            is_dfs=is_dfs,
            is_psc=is_psc,
            is_current=is_current,
            reasons=reasons,
        ))
    return out


def best_channel(scores: list[ChannelScore]) -> int | None:
    if not scores:
        return None
    # Highest score wins ; tiebreak by lowest channel number (stable
    # behaviour across reboots).
    sorted_scores = sorted(scores, key=lambda s: (-s.score, s.channel))
    return sorted_scores[0].channel


# ---------------------------- threats ---------------------------- #

def detect_threats(
    neighbors: list[NeighborAP],
    *,
    our_ssids: set[str],
    our_bssids: set[str],
    our_channels: dict[WifiBand, int],
) -> list[ThreatEvent]:
    """Apply heuristic rules over the neighbor list.

    Args:
        our_ssids: lowercased SSID names this controller is broadcasting.
            Used to flag evil-twin candidates.
        our_bssids: lowercased BSSIDs of our own VAPs. Used to exclude
            "ourselves" from the analysis.
        our_channels: band → our currently active channel. Used to flag
            strong-neighbor co-channel.
    """
    events: list[ThreatEvent] = []
    for n in neighbors:
        # Skip our own broadcasts.
        if n.bssid in our_bssids:
            continue
        # Evil-twin : foreign BSSID, same SSID name (case-insensitive,
        # stripped). Only fire on non-empty / non-hidden SSIDs.
        if n.ssid and n.ssid.strip().lower() in our_ssids:
            events.append(ThreatEvent(
                kind="evil_twin",
                level="alert",
                bssid=n.bssid,
                ssid=n.ssid,
                channel=n.channel,
                rssi_dbm=n.rssi_dbm,
                message=(
                    f"Evil twin suspecté : un AP étranger ({n.bssid}) "
                    f"diffuse ton SSID « {n.ssid} » sur le canal {n.channel}."
                ),
            ))
        # Legacy crypto : WEP or open or WPA1 broadcasting nearby.
        # Informational, doesn't impact your security directly but worth
        # surfacing for OSINT and to warn about neighbours.
        if n.security in {"WEP", "WPA1"}:
            events.append(ThreatEvent(
                kind="legacy_crypto",
                level="info",
                bssid=n.bssid,
                ssid=n.ssid or "<hidden>",
                channel=n.channel,
                rssi_dbm=n.rssi_dbm,
                message=(
                    f"AP voisin avec crypto déprecié ({n.security}) — "
                    f"« {n.ssid or 'hidden'} »."
                ),
            ))
        # WPS enabled : PixieDust surface. Worth flagging on OUR network
        # if we ever broadcast WPS, but here it's just OSINT.
        if n.is_wps_enabled and n.rssi_dbm > -75:
            events.append(ThreatEvent(
                kind="wps_enabled",
                level="info",
                bssid=n.bssid,
                ssid=n.ssid or "<hidden>",
                channel=n.channel,
                rssi_dbm=n.rssi_dbm,
                message=(
                    f"AP voisin proche avec WPS actif : « {n.ssid} » — "
                    "vecteur PixieDust possible si pin faible."
                ),
            ))
        # Strong same-channel neighbor : potential deauth source / interference.
        our_ch = our_channels.get(n.band)
        if our_ch and n.channel == our_ch and n.rssi_dbm > -55:
            events.append(ThreatEvent(
                kind="strong_neighbor",
                level="warn",
                bssid=n.bssid,
                ssid=n.ssid or "<hidden>",
                channel=n.channel,
                rssi_dbm=n.rssi_dbm,
                message=(
                    f"AP voisin très proche ({n.rssi_dbm} dBm) sur ton "
                    f"canal {n.channel} — risque interférence / deauth."
                ),
            ))
    return events


# ---------------------------- scan orchestration ---------------------------- #

# Per-band default scan iface. These are the OEM template sections we
# keep alive in the catalog-driven layout (ra0/rai0/rax0).
DEFAULT_SCAN_IFACE: dict[WifiBand, str] = {
    "2": "ra0",
    "5": "rai0",
    "6": "rax0",
}


async def scan_band(
    ssh: SlateSSH,
    band: WifiBand,
    *,
    iface: str | None = None,
    timeout_s: int = 25,
) -> ScanResult:
    """Run an active scan on the given band and return structured results.

    The scan does NOT bring the iface down — MTK supports background
    scan from a Master-mode iface. Some channels (DFS in 5 GHz) require
    a passive-scan window of ~100ms each, which is why the timeout is
    generous.
    """
    if iface is None:
        iface = DEFAULT_SCAN_IFACE[band]
    # Make sure the iface is up before we scan ; iw will refuse on a
    # down iface. ``ip link set <ifn> up`` is idempotent and cheap.
    await ssh.run(f"ip link set {iface} up 2>/dev/null; sleep 0.3", timeout=5)
    started = time.time()
    try:
        result = await ssh.run(f"iw dev {iface} scan 2>&1", timeout=timeout_s)
    except SlateSSHError as exc:
        raise RuntimeError(f"scan {band} GHz via {iface} failed: {exc}") from exc
    duration = time.time() - started
    raw = result.stdout if isinstance(result.stdout, str) else ""
    neighbors = parse_iw_scan(raw)
    # Drop neighbors not on this band — the scan reports ALL bands on
    # some firmware (Wi-Fi 7 dongles), keep filtering on band.
    neighbors = [n for n in neighbors if n.band == band]
    # Read our current channel from the same iface (best-effort).
    cur_ch = None
    try:
        info = await ssh.run(f"iw dev {iface} info 2>/dev/null", timeout=5)
        m = re.search(r"channel\s+(\d+)", info.stdout)
        if m:
            cur_ch = int(m.group(1))
    except SlateSSHError:
        pass
    scores = score_channels(band, neighbors, current_channel=cur_ch)
    rec = best_channel(scores)
    return ScanResult(
        band=band,
        iface=iface,
        started_at=started,
        duration_s=duration,
        neighbors=neighbors,
        channel_scores=scores,
        recommended_channel=rec,
        current_channel=cur_ch,
        threats=[],  # populated by the route after our SSIDs/BSSIDs are known
    )
