"""Tor control-port client (via SSH + ``nc`` on the device).

The Tor control protocol is a tiny text RPC. We don't keep a persistent
connection : each refresh batches AUTHENTICATE + N×GETINFO + QUIT into
one ``nc 127.0.0.1 9051`` invocation, piped over a single SSH round
trip. Latency is dominated by SSH (~80–200 ms), not by Tor.

Cookie authentication : Tor writes a 32-byte random cookie to
``/var/lib/tor/control_auth_cookie`` when ``CookieAuthentication 1`` is
set (we do in torrc). We hex-encode it and AUTHENTICATE with that.

Resilience : every public function returns "empty / None" on any error
(SSH failure, daemon down, cookie missing). The caller composes those
defaults into a :class:`TorStatus` — the UI gracefully shows "Tor down".

Spec reference : tor-spec.txt control-spec (latest at gitlab.torproject.org).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

DEFAULT_CONTROL_PORT = 9051
DEFAULT_COOKIE_PATH = "/var/lib/tor/control_auth_cookie"


@dataclass
class RelayHop:
    """One hop in a Tor circuit (entry / middle / exit)."""

    fingerprint: str
    nickname: str
    ip: str | None = None
    country: str | None = None       # ISO-3166-1 alpha-2, lowercase ("de")
    latitude: float | None = None
    longitude: float | None = None
    bandwidth_kbps: int | None = None


@dataclass
class Circuit:
    """A built Tor circuit (3 hops typically)."""

    circuit_id: str
    purpose: str
    build_flags: list[str]
    hops: list[RelayHop]


# ── Low-level : one batched control-port conversation per call ───────


def _ctrl_script(cookie_path: str, port: int, commands: list[str]) -> str:
    """Build a tiny shell script that hex-encodes the cookie and feeds
    AUTHENTICATE + the commands + QUIT to ``nc 127.0.0.1 <port>``.

    The whole printf format string must stay on ONE shell line — Python
    real newlines between commands would terminate the bash quoted string
    early and the leftover commands would be interpreted as shell
    commands (one famously emits `GETINFO traffic/read: not found`).
    Joining with the printf escape `\\r\\n` keeps it one-line and lets
    printf emit CRLF terminators, which is what the Tor control protocol
    expects between commands.

    Busybox ``hexdump -ve '/1 "%02x"' ...`` is portable across the GL.iNet
    firmware ; ``xxd`` isn't shipped.
    """
    body = "\\r\\n".join(commands)
    # BusyBox ``nc`` on this firmware doesn't support ``-w`` (closes
    # immediately on EOF). To give Tor time to reply, we wrap stdin in a
    # subshell with a trailing ``sleep`` so nc keeps the socket open
    # while the response arrives. 2 s is empirically enough for
    # circuit-status + per-relay ns/id queries on a healthy daemon.
    return (
        f"COOKIE=$(hexdump -ve '/1 \"%02x\"' {cookie_path} 2>/dev/null); "
        f"[ -z \"$COOKIE\" ] && exit 0; "
        f"(printf 'AUTHENTICATE %s\\r\\n{body}\\r\\nQUIT\\r\\n' \"$COOKIE\"; "
        f"sleep 2) | nc 127.0.0.1 {port} 2>/dev/null"
    )


async def _talk(
    ssh: SlateSSH,
    commands: list[str],
    *,
    cookie_path: str = DEFAULT_COOKIE_PATH,
    port: int = DEFAULT_CONTROL_PORT,
    timeout: float = 10.0,
) -> str:
    """Run a batched control-port conversation. Returns the raw response
    (empty string on any error).
    """
    script = _ctrl_script(cookie_path, port, commands)
    try:
        r = await ssh.run(script, timeout=timeout)
    except SlateSSHError as exc:
        logger.debug("tor.control.ssh_failed", error=str(exc))
        return ""
    return r.stdout or ""


# Tor reply codes : 250 OK / 250+key=value (multi-line) / 5xx errors.
# We just grep for the bits we care about — no need for a full parser.
_LINE_RE = re.compile(r"^(\d{3})([-+ ])(.*)$")


def _replies_block(raw: str, key: str) -> str:
    """Extract a 250+key=... block. Multi-line replies are terminated by
    a bare ``.`` line. Single-line replies (250-key=...) return the value
    directly.
    """
    lines = raw.splitlines()
    out: list[str] = []
    collecting = False
    for line in lines:
        m = _LINE_RE.match(line)
        if collecting:
            if line == ".":
                break
            out.append(line)
            continue
        if not m:
            continue
        code, sep, rest = m.group(1), m.group(2), m.group(3)
        if code != "250":
            continue
        # 250-key=value : single-line, value inline.
        # 250+key=     : multi-line, body follows.
        if sep == "-":
            if rest.startswith(f"{key}="):
                return rest[len(key) + 1 :]
        elif sep == "+":
            if rest.startswith(f"{key}="):
                # Body starts on the NEXT line (Tor convention).
                collecting = True
    return "\n".join(out)


# Each circuit-status line looks like :
#   <id> BUILT $FP1~nick1,$FP2~nick2,$FP3~nick3 BUILD_FLAGS=... PURPOSE=GENERAL
# We ignore non-BUILT states (UNDEF, LAUNCHED, EXTENDED, FAILED, CLOSED).
_PATH_RE = re.compile(r"\$([A-F0-9]{40})~([A-Za-z0-9_-]+)")


def _parse_circuits(block: str) -> list[Circuit]:
    out: list[Circuit] = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ")
        if len(parts) < 3:
            continue
        circ_id, state, path = parts[0], parts[1], parts[2]
        if state != "BUILT":
            continue
        purpose = ""
        flags: list[str] = []
        for tok in parts[3:]:
            if tok.startswith("PURPOSE="):
                purpose = tok[len("PURPOSE=") :]
            elif tok.startswith("BUILD_FLAGS="):
                flags = tok[len("BUILD_FLAGS=") :].split(",")
        hops = [
            RelayHop(fingerprint=fp, nickname=nick)
            for fp, nick in _PATH_RE.findall(path)
        ]
        if not hops:
            continue
        out.append(
            Circuit(
                circuit_id=circ_id, purpose=purpose,
                build_flags=flags, hops=hops,
            ),
        )
    return out


# ns/id/<fp> returns a router-status block whose 'r' line carries
#   r <nick> <id-base64> <descdigest> <pubdate> <pubtime> <ip> <orport> <dirport>
# 'p' Reject/Accept policy summary, 'w' Bandwidth, 's' Flags.
_NS_R_RE = re.compile(
    r"^r\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)\s+",
)
_NS_W_RE = re.compile(r"^w Bandwidth=(\d+)")


def _parse_ns_block(block: str) -> tuple[str | None, str | None, int | None]:
    """Extract (nickname, ip, bandwidth_kbps) from one ns/id/<fp> reply."""
    nick: str | None = None
    ip: str | None = None
    bw: int | None = None
    for line in block.splitlines():
        if line.startswith("r "):
            m = _NS_R_RE.match(line)
            if m:
                nick, ip = m.group(1), m.group(2)
        elif line.startswith("w "):
            m = _NS_W_RE.match(line)
            if m:
                bw = int(m.group(1))
    return nick, ip, bw


# ── High-level helpers ─────────────────────────────────────────────


async def fetch_circuits_and_traffic(
    ssh: SlateSSH,
) -> tuple[list[Circuit], int | None, int | None]:
    """One batched call : circuits, traffic/read, traffic/written.

    Returns (circuits, bytes_read, bytes_written). All optional :
    everything's None / empty when the daemon is down / cookie missing.
    """
    raw = await _talk(
        ssh,
        [
            "GETINFO circuit-status",
            "GETINFO traffic/read",
            "GETINFO traffic/written",
        ],
    )
    if not raw:
        return [], None, None

    circs = _parse_circuits(_replies_block(raw, "circuit-status"))

    def _int_or_none(s: str) -> int | None:
        s = s.strip()
        return int(s) if s.isdigit() else None

    bytes_read = _int_or_none(_replies_block(raw, "traffic/read"))
    bytes_written = _int_or_none(_replies_block(raw, "traffic/written"))
    return circs, bytes_read, bytes_written


async def enrich_with_ns(
    ssh: SlateSSH, circuits: list[Circuit],
) -> list[Circuit]:
    """Fill in (ip, bandwidth, country) for every hop, in one batched call.

    Builds a single GETINFO sequence with one ns/id/<fp> + one
    ip-to-country/<ip> per relay, sends it, parses each reply.
    """
    if not circuits:
        return circuits

    # Dedup fingerprints across circuits — relays often repeat.
    fps: list[str] = []
    seen: set[str] = set()
    for c in circuits:
        for h in c.hops:
            if h.fingerprint not in seen:
                seen.add(h.fingerprint)
                fps.append(h.fingerprint)
    if not fps:
        return circuits

    commands = [f"GETINFO ns/id/{fp}" for fp in fps]
    raw = await _talk(ssh, commands, timeout=15.0)
    if not raw:
        return circuits

    # Parse one ns block per fingerprint and stash IP/nick/bw.
    info: dict[str, tuple[str | None, str | None, int | None]] = {}
    for fp in fps:
        block = _replies_block(raw, f"ns/id/{fp}")
        info[fp] = _parse_ns_block(block) if block else (None, None, None)

    # Second batched call : country for every distinct IP we found.
    ips: list[str] = []
    seen_ip: set[str] = set()
    for nick, ip, _ in info.values():
        if ip and ip not in seen_ip:
            seen_ip.add(ip)
            ips.append(ip)

    countries: dict[str, str | None] = {}
    if ips:
        commands_c = [f"GETINFO ip-to-country/{ip}" for ip in ips]
        raw_c = await _talk(ssh, commands_c, timeout=10.0)
        for ip in ips:
            val = _replies_block(raw_c, f"ip-to-country/{ip}").strip().lower()
            # Tor returns "??" when the GeoIP file doesn't cover the IP.
            countries[ip] = val if val and val != "??" else None

    for c in circuits:
        for h in c.hops:
            nick, ip, bw = info.get(h.fingerprint, (None, None, None))
            if nick:
                h.nickname = nick
            h.ip = ip
            h.bandwidth_kbps = bw
            if ip:
                h.country = countries.get(ip)
    return circuits
