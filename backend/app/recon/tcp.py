"""TCP port probe + banner grab from the Slate.

We run probes on the Slate (not in the controller container) so that
the source IP is the Slate's interface address — that's what the
operator actually wants to observe ("from the slate, what do I see ?").

Two helpers :

- :func:`probe_ports` connects to each (ip, port) tuple via
  ``nc -z -w<timeout>``. The exit code tells us open / closed ; we
  classify everything that didn't error as "closed" rather than
  "filtered" because busybox ``nc`` can't distinguish (no RST visible
  in userland on the firmware). Filtering would need a real probe
  library (scapy / nmap).

- :func:`grab_banner` does a tiny non-intrusive read on an open
  port. The read strategy is service-specific :
    - SSH (22)        : the server speaks first ("SSH-2.0-...")
    - HTTP/HTTPS      : issue ``HEAD / HTTP/1.0\\r\\n\\r\\n`` and
      capture the response headers
    - everything else : drain whatever the server sends for 1 s
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from app.slate.ssh import SlateSSH, SlateSSHError

# Default port set : ssh, http, https, smb, vnc, rdp, alt-http,
# winrm-http, mysql, postgres, redis. Tweaked for the road-warrior
# hotel use-case : services that often run on other guests'
# laptops/IoT.
DEFAULT_PORTS: tuple[int, ...] = (
    22,
    23,
    53,
    80,
    111,
    135,
    139,
    443,
    445,
    554,
    631,
    1900,
    3000,
    3306,
    3389,
    5000,
    5432,
    5900,
    6379,
    8000,
    8008,
    8080,
    8443,
    8888,
    9000,
    32400,
)


# Map port → rough service guess. Only the obvious ones — a real
# fingerprint comes from the banner.
_SERVICE_HINT: dict[int, str] = {
    22: "ssh",
    23: "telnet",
    53: "dns",
    80: "http",
    111: "rpcbind",
    135: "msrpc",
    139: "smb",
    443: "https",
    445: "smb",
    554: "rtsp",
    631: "ipp",
    1900: "ssdp",
    3000: "http",
    3306: "mysql",
    3389: "rdp",
    5000: "http",
    5432: "postgres",
    5900: "vnc",
    6379: "redis",
    8000: "http",
    8008: "http",
    8080: "http",
    8443: "https",
    8888: "http",
    9000: "http",
    32400: "plex",
}


@dataclass(frozen=True)
class ProbedPort:
    """One TCP probe result."""

    ip: str
    port: int
    state: str  # open / closed / filtered
    banner: str
    service: str


async def _probe_single(
    ssh: SlateSSH, ip: str, port: int, timeout_s: float,
) -> bool:
    """Return True iff the (ip, port) accepts a TCP connect within timeout.

    Uses ``nc -z -w<timeout>`` — busybox netcat ships -w (connect
    timeout) and -z (zero-IO scan mode). Exit code 0 = open.
    """
    try:
        res = await ssh.run(
            f"nc -z -w{max(1, int(timeout_s))} {ip} {port} 2>/dev/null; echo $?",
            timeout=timeout_s + 2,
        )
    except SlateSSHError:
        return False
    rc_line = res.stdout.strip().splitlines()
    if not rc_line:
        return False
    try:
        return int(rc_line[-1]) == 0
    except ValueError:
        return False


async def probe_ports(
    ssh: SlateSSH,
    ips: list[str],
    ports: tuple[int, ...] = DEFAULT_PORTS,
    *,
    concurrency: int = 32,
    per_probe_timeout_s: float = 1.0,
    on_progress: "callable[[int, int], None] | None" = None,
) -> list[ProbedPort]:
    """Probe (ip × port) pairs ; return one ProbedPort per OPEN port.

    Closed / filtered ports are intentionally NOT returned : the
    target list would explode for nothing useful (a /24 × 26 ports
    = 6600 rows of "closed"). We only persist open ports.
    """
    tasks: list[tuple[str, int]] = [(ip, p) for ip in ips for p in ports]
    total = len(tasks)
    if total == 0:
        return []

    sem = asyncio.Semaphore(concurrency)
    done = 0
    opens: list[ProbedPort] = []

    async def _run(ip: str, port: int) -> None:
        nonlocal done
        async with sem:
            ok = await _probe_single(ssh, ip, port, per_probe_timeout_s)
            if ok:
                opens.append(
                    ProbedPort(
                        ip=ip,
                        port=port,
                        state="open",
                        banner="",
                        service=_SERVICE_HINT.get(port, ""),
                    )
                )
        done += 1
        if on_progress is not None and (done % 32 == 0 or done == total):
            on_progress(done, total)

    await asyncio.gather(*(_run(ip, p) for ip, p in tasks))
    opens.sort(key=lambda p: (tuple(int(o) for o in p.ip.split(".")), p.port))
    return opens


# ---------------------------- banner grab ---------------------------- #


_HEX_PUNCT = re.compile(r"[^\x20-\x7e\r\n\t]+")


def _sanitize_banner(raw: str) -> str:
    """Strip non-printables + cap length so the DB column stays clean."""
    cleaned = _HEX_PUNCT.sub("", raw).strip()
    cleaned = cleaned.replace("\r", "").replace("\n", " ")
    return cleaned[:256]


def _grab_command(ip: str, port: int) -> str:
    """Build the busybox-friendly shell snippet that captures a banner."""
    if port in (80, 8000, 8008, 8080, 8888, 9000, 3000, 5000):
        # HEAD request — most http stacks reply with Server header,
        # which is exactly the fingerprint we want.
        return (
            f"(printf 'HEAD / HTTP/1.0\\r\\nHost: x\\r\\n\\r\\n'; "
            f"sleep 1) | nc -w2 {ip} {port} 2>/dev/null | head -c 512"
        )
    if port == 443 or port == 8443:
        # busybox has no openssl s_client by default — we report the
        # port as open but skip the TLS banner. A future iteration
        # could use ``openssl s_client -connect ...`` if the package
        # is available, but the .12 firmware we target doesn't ship
        # the s_client tool either.
        return f"echo ''"
    if port == 22:
        # SSH server speaks first.
        return f"(sleep 1) | nc -w2 {ip} {port} 2>/dev/null | head -c 256"
    if port == 21:
        return f"(sleep 1) | nc -w2 {ip} {port} 2>/dev/null | head -c 256"
    if port in (25, 110, 143, 587):
        return f"(sleep 1) | nc -w2 {ip} {port} 2>/dev/null | head -c 256"
    # Generic : just drain whatever the server sends.
    return f"(sleep 1) | nc -w2 {ip} {port} 2>/dev/null | head -c 256"


_HTTP_SERVER = re.compile(r"^Server:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_HTTP_X_POWERED = re.compile(r"^X-Powered-By:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_SSH_GREET = re.compile(r"^SSH-\d+\.\d+-(.+)$", re.MULTILINE)


def _refine_service(port: int, banner: str) -> str:
    """Sharpen the service guess from the banner content."""
    if banner.startswith("SSH-"):
        m = _SSH_GREET.search(banner)
        return f"ssh ({m.group(1)[:32]})" if m else "ssh"
    if banner.upper().startswith("HTTP/"):
        m = _HTTP_SERVER.search(banner)
        if m:
            return f"http ({m.group(1)[:32].strip()})"
        m2 = _HTTP_X_POWERED.search(banner)
        if m2:
            return f"http ({m2.group(1)[:32].strip()})"
        return "http"
    return _SERVICE_HINT.get(port, "")


async def grab_banners(
    ssh: SlateSSH,
    opens: list[ProbedPort],
    *,
    concurrency: int = 16,
    on_progress: "callable[[int, int], None] | None" = None,
) -> list[ProbedPort]:
    """For each open port, attempt a banner grab. Returns a NEW list."""
    total = len(opens)
    if total == 0:
        return []
    sem = asyncio.Semaphore(concurrency)
    done = 0
    results: list[ProbedPort] = []

    async def _grab(p: ProbedPort) -> None:
        nonlocal done
        async with sem:
            cmd = _grab_command(p.ip, p.port)
            try:
                res = await ssh.run(cmd, timeout=6)
                raw = res.stdout or ""
            except SlateSSHError:
                raw = ""
            banner = _sanitize_banner(raw)
            service = _refine_service(p.port, banner) or p.service
            results.append(
                ProbedPort(
                    ip=p.ip, port=p.port, state=p.state,
                    banner=banner, service=service,
                )
            )
        done += 1
        if on_progress is not None and (done % 8 == 0 or done == total):
            on_progress(done, total)

    await asyncio.gather(*(_grab(p) for p in opens))
    results.sort(key=lambda p: (tuple(int(o) for o in p.ip.split(".")), p.port))
    return results
