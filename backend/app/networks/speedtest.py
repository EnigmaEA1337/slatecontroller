"""Live observability helpers (speedtest + public IP + active bridges/SSIDs),
run on the device via SSH.

Speedtest deliberately avoids installing extra packages (speedtest-cli,
librespeed, iperf3) — the Slate's busybox ``curl`` is enough to measure
throughput against Cloudflare's free speed endpoints, and ``ping`` is
built in.

Cloudflare endpoints used :
  - https://speed.cloudflare.com/__down?bytes=N    download a known size
  - https://speed.cloudflare.com/__up              POST upload sink

Public-IP / geo lookup :
  - https://ipinfo.io/json    returns { ip, country, city, region, org, loc }

Active bridges / SSIDs are derived from kernel state (``bridge link``) ;
the controller catalog says what *should* exist, but the kernel says
what's actually carrying packets right now — that's what the Dashboard
shows in its "X actifs / Y catalogués" hint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


# ── Public-IP / geo ─────────────────────────────────────────────────


@dataclass
class PublicIPInfo:
    ip: str | None = None
    country: str | None = None       # ISO 3166-1 alpha-2, uppercase ("US")
    city: str | None = None
    region: str | None = None
    org: str | None = None           # ISP / ASN org
    # latitude / longitude — useful for putting the WAN exit on a map
    latitude: float | None = None
    longitude: float | None = None


async def fetch_public_ip(ssh: SlateSSH) -> PublicIPInfo:
    """Run ``curl https://ipinfo.io/json`` on the Slate and parse.

    Always best-effort : on SSH error or unreachable, we return an empty
    :class:`PublicIPInfo`. The UI shows "—" rather than failing.
    """
    cmd = "curl -s --max-time 6 https://ipinfo.io/json 2>/dev/null"
    try:
        r = await ssh.run(cmd, timeout=10)
    except SlateSSHError as exc:
        logger.warning("speedtest.public_ip.ssh_failed", error=str(exc))
        return PublicIPInfo()
    out = (r.stdout or "").strip()
    if not out:
        return PublicIPInfo()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        logger.warning("speedtest.public_ip.parse_failed", body=out[:120])
        return PublicIPInfo()

    loc = data.get("loc") or ""
    lat: float | None = None
    lng: float | None = None
    if "," in loc:
        try:
            la, lo = loc.split(",", 1)
            lat, lng = float(la), float(lo)
        except ValueError:
            pass

    return PublicIPInfo(
        ip=str(data.get("ip") or "") or None,
        country=str(data.get("country") or "").upper() or None,
        city=str(data.get("city") or "") or None,
        region=str(data.get("region") or "") or None,
        org=str(data.get("org") or "") or None,
        latitude=lat,
        longitude=lng,
    )


# ── Speedtest ───────────────────────────────────────────────────────


@dataclass
class SpeedtestResult:
    ping_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None
    download_mbps: float | None = None
    upload_mbps: float | None = None
    server: str = "speed.cloudflare.com"
    bytes_downloaded: int | None = None
    bytes_uploaded: int | None = None
    error: str | None = None


_PING_LINE = re.compile(
    r"round-trip\s+min/avg/max(?:/mdev)?\s*=\s*"
    r"([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?",
)
_PING_LOSS = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*packet loss")


async def _ping(ssh: SlateSSH, target: str = "1.1.1.1") -> tuple[float | None, float | None, float | None]:
    """Run ``ping -c 5`` and parse min/avg/max + packet loss.

    Returns (avg_ms, jitter_ms, loss_pct). None for any field we can't
    parse (some busybox builds emit different formats — we try both).
    """
    cmd = f"ping -c 5 -W 2 {target} 2>&1"
    try:
        r = await ssh.run(cmd, timeout=15)
    except SlateSSHError:
        return None, None, None
    out = r.stdout or ""

    avg: float | None = None
    jitter: float | None = None
    loss: float | None = None
    m = _PING_LINE.search(out)
    if m:
        try:
            avg = float(m.group(2))
            if m.group(4):
                jitter = float(m.group(4))
        except ValueError:
            pass
    ml = _PING_LOSS.search(out)
    if ml:
        try:
            loss = float(ml.group(1))
        except ValueError:
            pass
    return avg, jitter, loss


async def _download_mbps(
    ssh: SlateSSH, bytes_size: int = 100_000_000,
) -> tuple[float | None, int | None]:
    """Time a download of `bytes_size` bytes from Cloudflare.

    Returns (mbps, bytes_actually_downloaded). curl's ``%{speed_download}``
    is bytes/s of the BODY (excluding TCP/TLS handshake), so it's the
    cleanest signal on a single connection.
    """
    url = f"https://speed.cloudflare.com/__down?bytes={bytes_size}"
    cmd = (
        f"curl -s --max-time 30 -o /dev/null "
        f"-w '%{{speed_download}} %{{size_download}}' '{url}' 2>/dev/null"
    )
    try:
        r = await ssh.run(cmd, timeout=45)
    except SlateSSHError:
        return None, None
    out = (r.stdout or "").strip()
    parts = out.split()
    if len(parts) != 2:
        return None, None
    try:
        bps = float(parts[0])     # bytes per second
        size = int(float(parts[1]))
    except ValueError:
        return None, None
    return (bps * 8.0 / 1_000_000.0), size


async def _upload_mbps(
    ssh: SlateSSH, bytes_size: int = 20_000_000,
) -> tuple[float | None, int | None]:
    """Time an upload of `bytes_size` random bytes to Cloudflare's __up.

    We feed ``dd if=/dev/urandom`` straight into curl's stdin so we don't
    have to materialise the test data on the Slate's tmpfs.
    """
    mb = max(1, bytes_size // (1024 * 1024))
    cmd = (
        f"dd if=/dev/urandom bs=1M count={mb} 2>/dev/null | "
        f"curl -s --max-time 30 -X POST -o /dev/null "
        f"-H 'Content-Type: application/octet-stream' "
        f"-w '%{{speed_upload}} %{{size_upload}}' "
        f"--data-binary @- 'https://speed.cloudflare.com/__up' 2>/dev/null"
    )
    try:
        r = await ssh.run(cmd, timeout=60)
    except SlateSSHError:
        return None, None
    out = (r.stdout or "").strip()
    parts = out.split()
    if len(parts) != 2:
        return None, None
    try:
        bps = float(parts[0])
        size = int(float(parts[1]))
    except ValueError:
        return None, None
    return (bps * 8.0 / 1_000_000.0), size


@dataclass
class ActiveSsid:
    ifname: str          # ra1, rai2, rax2, etc.
    ssid: str
    band: str            # "2g" / "5g" / "6g"
    bridge: str          # br-xxx the ifname is attached to


async def fetch_active_bridges(ssh: SlateSSH) -> list[str]:
    """Bridges that currently have at least one UP+forwarding member.

    The kernel's ``bridge link`` output is the source of truth — it
    reflects what's actually carrying traffic, regardless of what the
    controller catalog thinks should exist.
    """
    try:
        r = await ssh.run(
            "bridge link 2>/dev/null | grep forwarding "
            "| grep -oE 'master br-[a-z]+' | sort -u",
            timeout=8,
        )
    except SlateSSHError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("master "):
            continue
        name = line.split(" ", 1)[1].strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return sorted(out)


async def fetch_active_ssids(ssh: SlateSSH) -> list[ActiveSsid]:
    """SSIDs currently broadcasting (= a wifi-iface whose ifname is
    forwarding inside its target bridge).

    iwinfo ESSID is cached on this MTK driver (we proved it lies), so we
    walk the kernel state instead :
        1. ``bridge link``  → ifname:bridge pairs that are forwarding.
        2. For each ifname, look up its UCI section → ssid + device.
        3. Look up device.band → "2g" / "5g" / "6g".
    """
    script = (
        # Build a list of "ifname|bridge" pairs from forwarding members.
        "bridge link 2>/dev/null | grep forwarding | "
        "awk '{for(i=1;i<=NF;i++) if($i==\"master\") "
        "print substr($2,1,length($2)-1)\"|\"$(i+1)}' "
    )
    try:
        r = await ssh.run(script, timeout=10)
    except SlateSSHError:
        return []
    pairs = [
        ln.strip() for ln in (r.stdout or "").splitlines() if "|" in ln
    ]
    if not pairs:
        return []

    # Dedup ifnames, resolve each via uci.
    seen: set[str] = set()
    queries: list[tuple[str, str]] = []
    for p in pairs:
        ifn, br = p.split("|", 1)
        if ifn in seen:
            continue
        seen.add(ifn)
        queries.append((ifn, br))

    # Batched uci lookup. We dump all wifi-iface ifname+ssid+device once,
    # then dump all wifi-device band once — two SSH round-trips total.
    try:
        iface_dump = await ssh.run(
            "uci -q show wireless 2>/dev/null | "
            "grep -E '\\.(ifname|ssid|device)='", timeout=8,
        )
        device_dump = await ssh.run(
            "uci -q show wireless 2>/dev/null | grep -E '\\.band='",
            timeout=8,
        )
    except SlateSSHError:
        return []

    # Parse "wireless.<section>.ifname='<ifn>'" → section, and similar.
    sec_to_ifname: dict[str, str] = {}
    sec_to_ssid: dict[str, str] = {}
    sec_to_device: dict[str, str] = {}
    for line in (iface_dump.stdout or "").splitlines():
        m = re.match(
            r"^wireless\.([^.]+)\.(ifname|ssid|device)='?([^']*)'?\s*$",
            line.strip(),
        )
        if not m:
            continue
        sec, key, val = m.group(1), m.group(2), m.group(3)
        if key == "ifname":
            sec_to_ifname[sec] = val
        elif key == "ssid":
            sec_to_ssid[sec] = val
        elif key == "device":
            sec_to_device[sec] = val

    device_to_band: dict[str, str] = {}
    for line in (device_dump.stdout or "").splitlines():
        m = re.match(r"^wireless\.([^.]+)\.band='?([^']*)'?\s*$", line.strip())
        if m:
            device_to_band[m.group(1)] = m.group(2)

    ifname_to_sec = {v: k for k, v in sec_to_ifname.items()}

    out: list[ActiveSsid] = []
    for ifn, br in queries:
        sec = ifname_to_sec.get(ifn)
        if not sec:
            continue
        ssid = sec_to_ssid.get(sec, "")
        device = sec_to_device.get(sec, "")
        band = device_to_band.get(device, "")
        if not ssid:
            continue
        out.append(ActiveSsid(ifname=ifn, ssid=ssid, band=band, bridge=br))
    return out


async def run_speedtest(
    ssh: SlateSSH,
    *,
    # Cloudflare's __down endpoint silently caps just under 100 MB —
    # asking for 100_000_000 returns 1 byte. 50 MB is more than enough
    # to saturate a gigabit link for ~1 s and gives a stable reading.
    download_bytes: int = 50_000_000,     # ~50 MB
    upload_bytes: int = 20_000_000,       # ~20 MB
) -> SpeedtestResult:
    """End-to-end speedtest : ping + download + upload, in that order.

    Run sequentially so each phase has the link to itself (no
    contention). Total ~20-30 s on a decent link.
    """
    result = SpeedtestResult()
    try:
        avg, jitter, loss = await _ping(ssh)
        result.ping_ms = avg
        result.jitter_ms = jitter
        result.packet_loss_pct = loss

        dmbps, dsize = await _download_mbps(ssh, download_bytes)
        result.download_mbps = dmbps
        result.bytes_downloaded = dsize

        umbps, usize = await _upload_mbps(ssh, upload_bytes)
        result.upload_mbps = umbps
        result.bytes_uploaded = usize
    except Exception as exc:  # noqa: BLE001
        logger.warning("speedtest.failed", error=str(exc))
        result.error = str(exc)
    return result
