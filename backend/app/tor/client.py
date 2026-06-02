"""SSH-side helpers for the Tor subsystem : install + live status.

Stored config (per-network toggles, global daemon switch, bridges) lives in
the controller DB. This module bridges to the on-device state — what's
actually installed and running.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError
from app.tor.control import (
    Circuit,
    enrich_with_ns,
    fetch_circuits_and_traffic,
)
from app.tor.models import (
    TorCircuitInfo,
    TorInstallStatus,
    TorRelayHop,
    TorStatus,
)

logger = structlog.get_logger(__name__)

# Bootstrap line emitted by tor every few seconds while connecting :
#   Bootstrapped 30% (loading_status): Loading networkstatus consensus
_BOOTSTRAP_RE = re.compile(
    r"Bootstrapped\s+(\d{1,3})%\s*(?:\(([^)]+)\))?\s*[:.]?\s*(.*)$"
)

# Default ports (overridable in torrc — we surface the configured values
# in the status when possible). Mirror these in the agent's tor.sh.
DEFAULT_SOCKS_PORT = 9050
DEFAULT_CONTROL_PORT = 9051
DEFAULT_TRANS_PORT = 9040
DEFAULT_DNS_PORT = 5353


async def detect_install(ssh: SlateSSH) -> TorInstallStatus:
    """Which Tor packages are present on the device. Cheap : just a few
    ``command -v`` / file existence checks. Does NOT require Tor to be
    running.
    """
    script = (
        "command -v tor >/dev/null 2>&1 && echo TOR=1 || echo TOR=0; "
        "command -v obfs4proxy >/dev/null 2>&1 && echo OBFS=1 || echo OBFS=0; "
        "[ -f /usr/share/tor/geoip ] || [ -f /etc/tor/geoip ] && "
        "echo GEOIP=1 || echo GEOIP=0"
    )
    try:
        r = await ssh.run(script, timeout=10)
    except SlateSSHError as exc:
        logger.warning("tor.detect_install_failed", error=str(exc))
        return TorInstallStatus()
    out = r.stdout
    return TorInstallStatus(
        tor="TOR=1" in out,
        tor_geoipdb="GEOIP=1" in out,
        obfs4proxy="OBFS=1" in out,
    )


async def _is_running(ssh: SlateSSH) -> bool:
    # pgrep -x tor misses the binary on some GL.iNet builds (proctitle is
    # `/usr/sbin/tor --runasdaemon 0 -f /etc/tor/torrc`, comm may differ).
    # Try three signals in increasing breadth :
    #   pgrep -x tor          basename match
    #   pgrep -f /usr/sbin/tor full-cmdline match
    #   :9050 LISTEN          a SOCKS listener can ONLY come from tor on
    #                          this firmware ; the most reliable signal.
    try:
        r = await ssh.run(
            "pgrep -x tor >/dev/null 2>&1 && echo Y && exit 0; "
            "pgrep -f '/usr/sbin/tor' >/dev/null 2>&1 && echo Y && exit 0; "
            "netstat -tln 2>/dev/null | grep -qE ':9050[[:space:]].*LISTEN' "
            "&& echo Y || echo N",
            timeout=5,
        )
    except SlateSSHError:
        return False
    return "Y" in r.stdout


async def _bootstrap_status(ssh: SlateSSH) -> tuple[int | None, str | None]:
    """Parse the most recent ``Bootstrapped`` line from tor's notices log.

    Returns ``(percent, phase)`` — both None if no log / no match.
    """
    # Tor on OpenWrt typically logs to /var/log/tor/notices.log when the
    # `Log notice file` directive is set; fall back to ``logread`` for the
    # /etc/init.d/tor procd default (which goes to syslog).
    script = (
        "tail -n 200 /var/log/tor/notices.log 2>/dev/null | "
        "grep Bootstrapped | tail -n 1 ; "
        "logread 2>/dev/null | grep -E 'Tor.*Bootstrapped' | tail -n 1"
    )
    try:
        r = await ssh.run(script, timeout=8)
    except SlateSSHError:
        return None, None
    for line in r.stdout.splitlines():
        m = _BOOTSTRAP_RE.search(line)
        if m:
            try:
                pct = int(m.group(1))
            except ValueError:
                pct = None
            phase = (m.group(3) or m.group(2) or "").strip() or None
            return pct, phase
    return None, None


async def _control_port_reachable(ssh: SlateSSH) -> bool:
    """Reachability check. We don't open a real control connection here
    (that's what fetch_circuits_and_traffic does) — just check that
    something is listening on 9051. ``nc -z`` is unreliable on this
    busybox build, so we grep ``netstat`` instead.
    """
    try:
        r = await ssh.run(
            f"netstat -tln 2>/dev/null | "
            f"grep -qE ':{DEFAULT_CONTROL_PORT}[[:space:]].*LISTEN' "
            "&& echo Y || echo N",
            timeout=5,
        )
    except SlateSSHError:
        return False
    return "Y" in r.stdout


async def _uptime_seconds(ssh: SlateSSH) -> int | None:
    """Best-effort tor process uptime. ``/proc/<pid>/stat`` field 22 is
    start_time in clock ticks since boot ; combined with system uptime
    and ``getconf CLK_TCK`` we get a wall-clock duration.
    """
    script = (
        "pid=$(pgrep -x tor | head -n 1); "
        "[ -z \"$pid\" ] && exit 0; "
        "starttime=$(awk '{print $22}' /proc/$pid/stat 2>/dev/null); "
        "clk=$(getconf CLK_TCK 2>/dev/null || echo 100); "
        "btime=$(awk '/btime/ {print $2}' /proc/stat); "
        "now=$(date +%s); "
        "[ -z \"$starttime\" ] || [ -z \"$btime\" ] && exit 0; "
        "echo $((now - btime - starttime / clk))"
    )
    try:
        r = await ssh.run(script, timeout=5)
    except SlateSSHError:
        return None
    s = r.stdout.strip()
    if s.isdigit():
        return int(s)
    return None


def _circuit_to_public(c: Circuit) -> TorCircuitInfo:
    return TorCircuitInfo(
        circuit_id=c.circuit_id,
        purpose=c.purpose,
        build_flags=list(c.build_flags),
        hops=[
            TorRelayHop(
                fingerprint=h.fingerprint,
                nickname=h.nickname,
                ip=h.ip,
                country=h.country,
                bandwidth_kbps=h.bandwidth_kbps,
                latitude=h.latitude,
                longitude=h.longitude,
            )
            for h in c.hops
        ],
    )


async def fetch_status(ssh: SlateSSH) -> TorStatus:
    """Snapshot of the on-device Tor daemon.

    Everything is best-effort : if SSH fails or tor isn't installed the
    returned object has sensible defaults (running=False, circuits=[],
    etc.) and the UI shows "Tor not installed / down".

    When the daemon is up AND the control port answers, we batch one
    AUTHENTICATE + GETINFO sequence to pull circuits + traffic counters,
    then a second batch to enrich each unique hop with (IP, nickname,
    bandwidth, country). Two SSH round-trips total per refresh.
    """
    install = await detect_install(ssh)
    if not install.tor:
        return TorStatus(install=install, last_probe_at=datetime.now(UTC))

    running = await _is_running(ssh)
    if not running:
        return TorStatus(
            install=install,
            daemon_running=False,
            last_probe_at=datetime.now(UTC),
        )

    bootstrap_pct, bootstrap_phase = await _bootstrap_status(ssh)
    ctrl = await _control_port_reachable(ssh)
    uptime = await _uptime_seconds(ssh)

    circuits_public: list[TorCircuitInfo] = []
    bytes_read: int | None = None
    bytes_written: int | None = None
    if ctrl:
        try:
            circs, bytes_read, bytes_written = await fetch_circuits_and_traffic(ssh)
            circs = await enrich_with_ns(ssh, circs)
            circuits_public = [_circuit_to_public(c) for c in circs]
        except Exception as exc:  # noqa: BLE001
            logger.warning("tor.control_query_failed", error=str(exc))

    # Exit info : pick the most recent GENERAL-purpose circuit's exit hop,
    # if any. That's the country/IP a request right now would come from.
    exit_ip: str | None = None
    exit_country: str | None = None
    for c in reversed(circuits_public):
        if c.purpose in ("GENERAL", "HS_CLIENT_INTRO", ""):
            if c.hops:
                exit_ip = c.hops[-1].ip
                exit_country = c.hops[-1].country
                break

    return TorStatus(
        install=install,
        daemon_running=True,
        control_port_reachable=ctrl,
        bootstrap_progress=bootstrap_pct,
        bootstrap_phase=bootstrap_phase,
        socks_port=DEFAULT_SOCKS_PORT,
        trans_port=None,
        dns_port=None,
        exit_ip=exit_ip,
        exit_country=exit_country,
        circuits=circuits_public,
        uptime_seconds=uptime,
        bytes_read=bytes_read,
        bytes_written=bytes_written,
        last_probe_at=datetime.now(UTC),
    )


# ── Install (opkg) ────────────────────────────────────────────────────


class TorInstallError(Exception):
    """Raised when the opkg install fails (no WAN, repo unreachable, etc.)."""


async def install_packages(ssh: SlateSSH) -> str:
    """Run ``opkg update && opkg install tor tor-geoipdb obfs4proxy``.

    Returns the merged stdout/stderr of the install so the route can
    surface it to the UI. Raises :class:`TorInstallError` on non-zero
    exit. Slow : 30-90 s depending on the WAN link.
    """
    cmd = (
        "opkg update 2>&1 && "
        "opkg install --force-overwrite tor tor-geoipdb obfs4proxy 2>&1"
    )
    try:
        r = await ssh.run(cmd, timeout=180)
    except SlateSSHError as exc:
        raise TorInstallError(f"SSH error during opkg install: {exc}") from exc
    if r.exit_status != 0:
        raise TorInstallError(
            f"opkg install exited {r.exit_status}: {r.stdout[-400:]}"
        )
    return r.stdout
