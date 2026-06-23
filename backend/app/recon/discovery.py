"""Host discovery : ARP cache + ping sweep.

Two helpers :

- :func:`read_arp_cache` reads ``ip neigh show dev <iface>`` and
  returns the live ``IP → MAC`` pairs the Slate already knows about.
  No probes sent — pure cheap lookup, useful both as a first pass
  and as a post-ping sniff.

- :func:`ping_sweep` walks the addressable hosts in a subnet and
  fires concurrent ``ping -c1 -W1 -I <iface> <ip>`` for each. The
  ping is the wake-up : the kernel ARP-resolves the host as a side
  effect, so a follow-up :func:`read_arp_cache` picks up everyone
  who answered (and most who didn't but were ARP-resolved).

Why ICMP instead of raw ARP probes : busybox / dropbear / GL.iNet
firmware ship neither ``arping`` nor ``arp-scan`` (both require
opkg install + >1MB). ``ping`` is always there and ARP-resolves
silently when the target is local.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass

from app.slate.ssh import SlateSSH, SlateSSHError

# ``ip neigh show dev <iface>`` outputs one line per neighbour :
#   192.168.8.42 lladdr aa:bb:cc:dd:ee:ff REACHABLE
# State can also be STALE / DELAY / PROBE / FAILED. We accept any
# state EXCEPT FAILED + INCOMPLETE — those are entries the kernel
# tried to resolve but didn't get an answer for, so the MAC is
# meaningless.
_NEIGH_LINE = re.compile(
    r"^(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?:dev\s+\S+\s+)?lladdr\s+(?P<mac>[0-9a-f:]{17})\s+(?P<state>\S+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscoveredHost:
    """A bare-bones host record from ARP/ping discovery.

    ``source`` is "arp" for cache pickups, "ping" for hosts that
    answered ICMP, "both" when fused after the post-sweep cache
    re-read.
    """

    ip: str
    mac: str
    source: str


async def read_arp_cache(ssh: SlateSSH, iface: str) -> list[DiscoveredHost]:
    """Read the ARP cache for one interface. Cheap, no probes sent."""
    try:
        res = await ssh.run(
            f"ip -4 neigh show dev {iface} 2>/dev/null", timeout=5,
        )
    except SlateSSHError:
        return []
    out: list[DiscoveredHost] = []
    for line in res.stdout.splitlines():
        m = _NEIGH_LINE.match(line.strip())
        if not m:
            continue
        if m.group("state").upper() in {"FAILED", "INCOMPLETE"}:
            continue
        out.append(DiscoveredHost(ip=m.group("ip"), mac=m.group("mac"), source="arp"))
    return out


def _hosts_for_sweep(cidr: str, slate_ip: str) -> list[str]:
    """Return the addressable hosts in ``cidr``, minus the Slate itself."""
    net = ipaddress.ip_network(cidr, strict=False)
    return [str(h) for h in net.hosts() if str(h) != slate_ip]


async def _ping_one(
    ssh: SlateSSH, iface: str, ip: str, timeout_s: float,
) -> str | None:
    """Single ICMP probe. Returns the IP on success, None on failure.

    Uses ``-c1 -W <timeout>`` for one packet with a hard cap. ``-I
    <iface>`` forces egress on the right NIC even when several have
    routes to the target."""
    try:
        res = await ssh.run(
            f"ping -c1 -W{int(timeout_s)} -I {iface} {ip} >/dev/null 2>&1; echo $?",
            timeout=timeout_s + 2,
        )
    except SlateSSHError:
        return None
    rc_line = res.stdout.strip().splitlines()
    if not rc_line:
        return None
    try:
        return ip if int(rc_line[-1]) == 0 else None
    except ValueError:
        return None


async def ping_sweep(
    ssh: SlateSSH,
    iface: str,
    cidr: str,
    slate_ip: str,
    *,
    concurrency: int = 24,
    per_host_timeout_s: float = 1.0,
    on_progress: "callable[[int, int], None] | None" = None,
) -> list[str]:
    """Ping every host in ``cidr`` and return the IPs that answered.

    ``concurrency`` caps parallel pings so we don't drown the SSH
    pipe ; 24 keeps a /24 sweep around ~12s on a healthy uplink.
    ``on_progress`` is called as ``(done, total)`` so the runner can
    update the persisted progress string.
    """
    targets = _hosts_for_sweep(cidr, slate_ip)
    total = len(targets)
    if total == 0:
        return []

    sem = asyncio.Semaphore(concurrency)
    done = 0
    responders: list[str] = []

    async def _probe(ip: str) -> None:
        nonlocal done
        async with sem:
            r = await _ping_one(ssh, iface, ip, per_host_timeout_s)
            if r:
                responders.append(r)
        done += 1
        if on_progress is not None and (done % 16 == 0 or done == total):
            on_progress(done, total)

    await asyncio.gather(*(_probe(ip) for ip in targets))
    return responders


def fuse(
    arp_first: list[DiscoveredHost],
    pinged_ips: list[str],
    arp_second: list[DiscoveredHost],
) -> list[DiscoveredHost]:
    """Merge ARP-cache + ping-sweep + post-sweep ARP into one host list.

    Tags each host with the union of where it was seen :
      - in arp_first only  → ``arp``
      - answered ping only → ``ping`` (no MAC if the ARP cache didn't
        pick it up for some reason)
      - in both            → ``both``
    """
    by_ip: dict[str, DiscoveredHost] = {}
    for h in arp_first:
        by_ip[h.ip] = h
    for h in arp_second:
        prev = by_ip.get(h.ip)
        if prev is None or not prev.mac:
            by_ip[h.ip] = h
    pinged_set = set(pinged_ips)
    out: list[DiscoveredHost] = []
    for ip, h in by_ip.items():
        src = "both" if ip in pinged_set else "arp"
        out.append(DiscoveredHost(ip=ip, mac=h.mac, source=src))
    for ip in pinged_set:
        if ip in by_ip:
            continue
        out.append(DiscoveredHost(ip=ip, mac="", source="ping"))
    out.sort(key=lambda h: tuple(int(o) for o in h.ip.split(".")))
    return out
