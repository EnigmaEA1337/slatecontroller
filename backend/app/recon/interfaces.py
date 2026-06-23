"""Enumerate L3 interfaces and their /N subnets on the Slate.

Exposes :func:`list_active_interfaces` which returns one
:class:`ReconInterface` per active L3 interface (WAN uplink + every
bridge that has an IPv4 address). Used by the API route as the source
of truth for "what can the operator pick to scan" and by the runner
to drive the sweep.

We deliberately limit ourselves to IPv4 + /N where N >= 22 (so a
sweep stays under ~1024 hosts). Larger subnets are reported but
flagged unscannable to avoid a 30-minute sweep blocking the runner.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from app.slate.ssh import SlateSSH, SlateSSHError

# ``ip -o -4 addr show`` lines look like :
#   3: br-lan    inet 192.168.8.1/24 brd 192.168.8.255 scope global br-lan\       valid_lft forever preferred_lft forever
_ADDR_LINE = re.compile(
    r"^\s*\d+:\s+(?P<iface>[\w.\-]+)\s+inet\s+(?P<cidr>\d+\.\d+\.\d+\.\d+/\d+)"
)


# WAN-family interfaces — match against the iface name to flag the
# "this is the uplink" rows in the UI.
_WAN_NAMES = frozenset({
    "wan", "wan6", "wwan", "wwan6", "eth1", "eth1.2",
    "tethering", "tethering6", "apclii0", "apcli0", "apclix0",
    "usb0", "wwan0",
})

# Largest scannable prefix. /22 = 1024 hosts. Anything wider is a
# config error or a corporate /16 — refusing protects the runner from
# a sweep that would never finish.
MAX_PINGABLE_PREFIX = 22


@dataclass(frozen=True)
class ReconInterface:
    """One L3 interface with enough metadata to drive a scan."""

    name: str
    ipv4_cidr: str  # e.g. "192.168.8.1/24"
    family: str  # "wan" / "lan" / "guest" / "other"
    host_count: int  # number of addressable hosts in the declared subnet
    scannable: bool  # always True now ; runner clamps wide subnets (see scan_cidr)
    gateway: str  # default gateway IP via this iface, "" if none
    # Subnet the runner will ACTUALLY sweep. Equals ipv4_cidr when the
    # operator-facing subnet fits under MAX_PINGABLE_PREFIX ; otherwise
    # the /24 around the Slate's own IP (the immediate L2 neighbours,
    # which matches the hotel-WiFi use case : the hotel hands out IPs
    # on a flat /16 but each guest only ever sees their own /24 worth
    # of physical neighbours). Operator can override via a future
    # custom-CIDR field.
    scan_cidr: str
    # True iff scan_cidr was clamped down from a wider ipv4_cidr.
    # The UI surfaces this with a "limité au /24 autour du Slate" note.
    scan_clamped: bool

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.ipv4_cidr, strict=False)

    @property
    def scan_network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.scan_cidr, strict=False)

    @property
    def slate_ip(self) -> str:
        return self.ipv4_cidr.split("/")[0]


def _classify(name: str) -> str:
    if name in _WAN_NAMES or name.startswith(("wan", "wwan", "tethering")):
        return "wan"
    if name.startswith("br-"):
        # Heuristic : br-lan / br-mission / br-vacances → "lan",
        # br-guest* → "guest".
        tail = name.removeprefix("br-").lower()
        if "guest" in tail or "iot" in tail:
            return "guest"
        return "lan"
    if name.startswith(("eth", "lan")):
        return "lan"
    return "other"


def _parse_addr_output(text: str) -> list[tuple[str, str]]:
    """Return ``[(iface, cidr), ...]`` from ``ip -o -4 addr show``."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _ADDR_LINE.match(line)
        if not m:
            continue
        iface = m.group("iface")
        cidr = m.group("cidr")
        # Skip loopback + obvious docker/tap noise — they're never
        # interesting recon targets.
        if iface == "lo" or iface.startswith(("docker", "veth", "tun")):
            continue
        out.append((iface, cidr))
    return out


def _parse_routes(text: str) -> dict[str, str]:
    """Return ``{iface: default_gateway_ip}`` from ``ip -4 route show``.

    The default route looks like :
        default via 192.168.8.1 dev br-lan ...
    A directly-attached subnet has no ``via`` — those don't have a
    gateway from our POV.
    """
    gws: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default":
            continue
        try:
            via_idx = parts.index("via")
            dev_idx = parts.index("dev")
            gws[parts[dev_idx + 1]] = parts[via_idx + 1]
        except (ValueError, IndexError):
            continue
    return gws


async def list_active_interfaces(ssh: SlateSSH) -> list[ReconInterface]:
    """Return one :class:`ReconInterface` per active L3 iface on the Slate."""
    try:
        addr_res = await ssh.run("ip -o -4 addr show 2>/dev/null", timeout=10)
        route_res = await ssh.run("ip -4 route show 2>/dev/null", timeout=10)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH probe failed: {exc}") from exc

    pairs = _parse_addr_output(addr_res.stdout)
    gws = _parse_routes(route_res.stdout)

    out: list[ReconInterface] = []
    for iface, cidr in pairs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        prefix = net.prefixlen
        host_count = max(0, net.num_addresses - 2)  # minus network + broadcast
        # Clamp wider-than-/MAX subnets down to a /24 around the Slate's
        # own IP. A hotel hands out IPs on a flat /16 but each guest
        # only ever shares L2 with their own /24 worth of neighbours,
        # so the clamped scan still surfaces what the operator cares
        # about (who's around me right now ?) — without spending 18h
        # pinging a 65k-host subnet.
        slate_ip_str = cidr.split("/")[0]
        if prefix < MAX_PINGABLE_PREFIX:
            try:
                slate_addr = ipaddress.ip_address(slate_ip_str)
                clamped = ipaddress.ip_network(f"{slate_addr}/24", strict=False)
                scan_cidr = str(clamped)
                scan_clamped = True
            except ValueError:
                scan_cidr = cidr
                scan_clamped = False
        else:
            scan_cidr = cidr
            scan_clamped = False
        out.append(
            ReconInterface(
                name=iface,
                ipv4_cidr=cidr,
                family=_classify(iface),
                host_count=host_count,
                # All interfaces are scannable now — the clamp above
                # turns "too wide to sweep" into "sweep the clamped /24
                # only". The operator's intent is preserved (recon on
                # this interface) while the sweep stays under a couple
                # of minutes.
                scannable=True,
                gateway=gws.get(iface, ""),
                scan_cidr=scan_cidr,
                scan_clamped=scan_clamped,
            )
        )
    # WAN family first (operator's primary concern), then LAN, guest, other.
    family_order = {"wan": 0, "lan": 1, "guest": 2, "other": 3}
    out.sort(key=lambda i: (family_order.get(i.family, 9), i.name))
    return out
