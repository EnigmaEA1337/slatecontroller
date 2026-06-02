"""Tor gateway security audit — local read-only checks.

The Slate's Tor stack is a CLIENT (transparent gateway) not a relay/exit,
so most of the audit focuses on :
  - keeping our daemon's surface area off the WAN / off other LANs ;
  - making sure no leak path bypasses Tor (IPv6, UDP/ICMP, conntrack
    survivors, etc.).

Findings follow the Tailscale-audit dataclass shape (id / label / status /
severity / evidence / remediation) so the UI can render them with the
same SecurityHardening card style.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from typing import Literal

import structlog

from app.networks.models import NetworkPublic
from app.networks.store import NetworkStore
from app.slate.ssh import SlateSSH, SlateSSHError
from app.tor.client import detect_install

logger = structlog.get_logger(__name__)

Severity = Literal["critical", "high", "medium", "low", "info", "pass"]
CheckStatus = Literal["pass", "fail", "warn", "info", "skip"]

_PENALTY: dict[Severity, int] = {
    "critical": 25,
    "high": 10,
    "medium": 5,
    "low": 2,
    "info": 0,
    "pass": 0,
}


@dataclass
class AuditFinding:
    id: str
    label: str
    status: CheckStatus
    severity: Severity
    evidence: str = ""
    remediation: str = ""


@dataclass
class TorAuditReport:
    score: int                          # 0-100, 100 = clean
    findings: list[AuditFinding] = field(default_factory=list)
    tor_running: bool = False
    tor_installed: bool = False
    transparent_networks: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Low-level probes ────────────────────────────────────────────────


async def _ssh_or_empty(ssh: SlateSSH, cmd: str, *, timeout: float = 8) -> str:
    try:
        r = await ssh.run(cmd, timeout=timeout)
    except SlateSSHError:
        return ""
    return r.stdout or ""


async def _listening_bindings(ssh: SlateSSH) -> dict[int, list[str]]:
    """Map tcp port → list of bound addresses ("0.0.0.0", "127.0.0.1",
    "10.183.7.1", ...). UDP shows up as "udp" listeners which `netstat
    -tlnu` reports separately ; we collapse them under the same key.

    Empty dict on probe failure — every check then SKIPs cleanly.
    """
    out = await _ssh_or_empty(ssh, "netstat -tlnu 2>/dev/null")
    bound: dict[int, list[str]] = {}
    for line in out.splitlines():
        # tcp        0      0 0.0.0.0:9050            0.0.0.0:*               LISTEN
        m = re.search(r"\b([\d.:a-fA-F]+):(\d+)\s+\S+\s+(LISTEN)?\b", line)
        if not m:
            continue
        addr, port_s = m.group(1), m.group(2)
        try:
            port = int(port_s)
        except ValueError:
            continue
        bound.setdefault(port, []).append(addr)
    return bound


async def _ip6tables_forward(ssh: SlateSSH) -> str:
    """Raw ip6tables FORWARD chain (numeric + verbose, so we get the
    `in` column needed to identify per-bridge DROP rules).
    """
    return await _ssh_or_empty(ssh, "ip6tables -L FORWARD -nv 2>/dev/null")


async def _filter_chain(ssh: SlateSSH, name: str) -> str:
    return await _ssh_or_empty(ssh, f"iptables -L {name} -nv 2>/dev/null")


async def _nat_chain(ssh: SlateSSH, name: str) -> str:
    return await _ssh_or_empty(ssh, f"iptables -t nat -L {name} -nv 2>/dev/null")


_CONNTRACK_SRC_RE = re.compile(r"\bsrc=(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
_CONNTRACK_DPORT_RE = re.compile(r"\bdport=(\d+)\b")


async def _conntrack_orphans_count(
    ssh: SlateSSH, subnet_cidr: str,
) -> int | None:
    """Count established TCP conntrack entries SOURCED FROM the given
    network's subnet that did NOT exit via Tor's TransPort (9040).

    Why subnet-aware filtering matters : an earlier revision matched
    any `src=10.*` which is a vast overcount on multi-subnet profiles
    — a non-Tor-routed network sharing the 10.0.0.0/8 space (e.g. a
    NEXUS-7 guest bridge) was incorrectly accused of leaking. Live
    profiles can mix one Tor-routed bridge with several non-Tor ones,
    so we filter strictly by the bridge's own CIDR.

    We pull the kernel's full conntrack table once, then filter in
    Python using `ipaddress.ip_network` so any prefix length works
    (not just /8, /16, /24). For a router with a few hundred active
    flows this stays microsecond-scale.

    Returns None when the probe fails (no /proc/net/nf_conntrack
    readable / SSH timeout), so the UI can render "needs_probe" rather
    than a fake zero.
    """
    try:
        net = ip_network(subnet_cidr, strict=False)
    except (ValueError, TypeError):
        return None
    out = await _ssh_or_empty(
        ssh, "grep -E '^ipv4.*tcp.*ESTABLISHED' /proc/net/nf_conntrack 2>/dev/null",
    )
    if not out:
        # Either no matching entries or the file is unreadable. We can't
        # tell the two apart from an empty string, so default to 0 ;
        # downstream check renders as "pass" which is the safe default
        # (matches what an empty conntrack table would show).
        return 0
    count = 0
    for line in out.splitlines():
        # Skip the bidirectional reply line — conntrack emits ONE
        # original direction (src/dst as seen on inbound) followed by
        # the reply ; we only count the original, which is the line
        # that has BOTH src= and dport= before any second occurrence.
        src_m = _CONNTRACK_SRC_RE.search(line)
        dport_m = _CONNTRACK_DPORT_RE.search(line)
        if not src_m or not dport_m:
            continue
        if dport_m.group(1) == "9040":
            continue
        try:
            ip = ip_address(src_m.group(1))
        except ValueError:
            continue
        if ip in net:
            count += 1
    return count


# ── Check implementations ──────────────────────────────────────────


def _check_port_bindings(
    bindings: dict[int, list[str]],
    transparent_gws: list[str],
) -> list[AuditFinding]:
    """SOCKS / TransPort / DNSPort must bind to a per-network gateway,
    never 0.0.0.0. ControlPort must stay on 127.0.0.1.
    """
    out: list[AuditFinding] = []
    expectations = {
        9050: ("Socks", "SOCKS proxy"),
        9040: ("TransPort", "transparent TCP redirect"),
        5353: ("DNSPort", "DNS-over-Tor redirect"),
    }
    for port, (role, label) in expectations.items():
        addrs = bindings.get(port) or []
        if not addrs:
            out.append(AuditFinding(
                id=f"tor.port.{port}.bind",
                label=f"Tor {role} {port} — listener absent",
                status="info",
                severity="info",
                evidence=f"Aucun listener sur :{port} (Tor probablement arrêté).",
            ))
            continue
        bad = [a for a in addrs if a in {"0.0.0.0", "::"}]
        if bad:
            out.append(AuditFinding(
                id=f"tor.port.{port}.bind",
                label=f"Tor {role} {port} exposé sur {bad[0]}",
                status="fail",
                severity="high",
                evidence=(
                    f"netstat : {role} bind sur {bad[0]}:{port}. "
                    "N'importe quel client de N'IMPORTE QUEL bridge (et le WAN si "
                    "le firewall a une faille) peut utiliser le Tor du Slate "
                    f"comme proxy ouvert ({label})."
                ),
                remediation=(
                    "Configurer torrc avec une directive par-gateway : "
                    f"`{role}Port <gw>:{port}` pour chaque réseau Tor-routé, "
                    "et supprimer la directive 0.0.0.0."
                ),
            ))
        else:
            out.append(AuditFinding(
                id=f"tor.port.{port}.bind",
                label=f"Tor {role} {port} bind correct",
                status="pass",
                severity="pass",
                evidence=f"bind = {', '.join(addrs)}",
            ))

    # ControlPort
    ctrl = bindings.get(9051) or []
    if not ctrl:
        out.append(AuditFinding(
            id="tor.port.9051.bind",
            label="Tor ControlPort absent",
            status="info",
            severity="info",
            evidence="Pas de listener sur 9051 (Tor arrêté ou ControlPort désactivé).",
        ))
    elif all(a in {"127.0.0.1", "::1"} for a in ctrl):
        out.append(AuditFinding(
            id="tor.port.9051.bind",
            label="Tor ControlPort bind loopback (correct)",
            status="pass",
            severity="pass",
            evidence=f"bind = {', '.join(ctrl)}",
        ))
    else:
        out.append(AuditFinding(
            id="tor.port.9051.bind",
            label=f"Tor ControlPort exposé sur {ctrl[0]}",
            status="fail",
            severity="critical",
            evidence=(
                f"ControlPort doit rester sur 127.0.0.1 : actuellement {ctrl[0]}. "
                "Un attaquant peut envoyer GETINFO / SETCONF et lire vos circuits."
            ),
            remediation="torrc : `ControlPort 127.0.0.1:9051` + `CookieAuthentication 1`.",
        ))
    return out


def _check_ipv6_leak(
    ip6tables_forward: str,
    transparent_nets: list[NetworkPublic],
) -> AuditFinding:
    """Each transparent network needs an ip6tables FORWARD DROP rule
    targeting its bridge — Tor doesn't carry IPv6, so without this any
    AAAA-resolved destination leaks the real public IPv6 via WAN.
    """
    if not transparent_nets:
        return AuditFinding(
            id="tor.ipv6.forward_drop",
            label="Anti-fuite IPv6 — aucun réseau transparent",
            status="info",
            severity="info",
            evidence="Aucun réseau en tor_route_mode=transparent : pas de risque IPv6.",
        )

    missing = []
    for net in transparent_nets:
        # iptables -nv output has counters at the start, so we don't
        # anchor to ^ — just look for any line where DROP appears with
        # this bridge as in-interface.
        pattern = re.compile(
            rf"\bDROP\b[^\n]*\b{re.escape(net.bridge_name)}\b",
        )
        if not pattern.search(ip6tables_forward):
            missing.append(net.bridge_name)

    if not missing:
        return AuditFinding(
            id="tor.ipv6.forward_drop",
            label="Anti-fuite IPv6 — règles présentes",
            status="pass",
            severity="pass",
            evidence=(
                f"ip6tables FORWARD a une règle DROP pour chacun des "
                f"{len(transparent_nets)} bridges Tor-routés."
            ),
        )
    return AuditFinding(
        id="tor.ipv6.forward_drop",
        label="Fuite IPv6 possible",
        status="fail",
        severity="high",
        evidence=(
            "Tor proxy uniquement IPv4. Sans drop IPv6 explicite, tout "
            "trafic IPv6 sortant (AAAA, ICMPv6, ...) bypass Tor et exposera "
            "votre IPv6 publique réelle. "
            f"Bridges sans DROP : {', '.join(missing)}."
        ),
        remediation=(
            "Pour chaque bridge transparent : "
            "`ip6tables -A FORWARD -i br-<slug> -j DROP`."
        ),
    )


def _check_kill_switch(
    iptables_kill: str,
    transparent_nets: list[NetworkPublic],
) -> AuditFinding:
    """SC_TOR_KILL must DROP all non-localhost-destined traffic from each
    transparent bridge. Catches UDP/ICMP/raw IP leaks.
    """
    if not transparent_nets:
        return AuditFinding(
            id="tor.kill_switch",
            label="Kill-switch — aucun réseau transparent",
            status="info",
            severity="info",
            evidence="Pas de risque tant que tor_route_mode=off partout.",
        )

    missing = []
    for net in transparent_nets:
        # Same iptables -nv counter prefix as for the IPv6 chain — match
        # without anchoring to ^.
        if not re.search(
            rf"\bDROP\b[^\n]*\b{re.escape(net.bridge_name)}\b", iptables_kill,
        ):
            missing.append(net.bridge_name)

    if not missing:
        return AuditFinding(
            id="tor.kill_switch",
            label="Kill-switch actif sur tous les bridges transparents",
            status="pass",
            severity="pass",
            evidence="SC_TOR_KILL contient une règle DROP par bridge.",
        )
    # Some networks may have opted out (tor_kill_switch=false). That's
    # legitimate but worth flagging as medium risk.
    return AuditFinding(
        id="tor.kill_switch",
        label="Kill-switch désactivé sur certains réseaux transparents",
        status="warn",
        severity="medium",
        evidence=(
            f"Pas de DROP catch-all dans SC_TOR_KILL pour : "
            f"{', '.join(missing)}. UDP/ICMP/raw IP et toute connexion "
            "TCP que Tor ne sait pas relayer sortiront en clair par le "
            "WAN — c'est une fuite d'IP réelle."
        ),
        remediation=(
            "Activer `tor_kill_switch=true` sur chaque réseau en routage "
            "transparent (page Réseaux → édition du réseau → onglet Tor)."
        ),
    )


def _check_conntrack_orphans(
    orphans: dict[str, int | None],
) -> AuditFinding:
    """Count established TCP connections from tor-routed bridges that
    DIDN'T go through TransPort 9040 — those are pre-existing connections
    that survived the apply and continue to leak.
    """
    if not orphans:
        return AuditFinding(
            id="tor.conntrack.orphans",
            label="Connexions orphelines — aucun réseau transparent",
            status="info",
            severity="info",
            evidence="Pas de risque : aucun réseau en routage transparent.",
        )

    bad = {b: n for b, n in orphans.items() if n is not None and n > 0}
    if not bad:
        return AuditFinding(
            id="tor.conntrack.orphans",
            label="Connexions orphelines — aucune détectée",
            status="pass",
            severity="pass",
            evidence="Toutes les connexions TCP actives passent par TransPort:9040.",
        )
    total = sum(bad.values())
    return AuditFinding(
        id="tor.conntrack.orphans",
        label=f"{total} connexion(s) TCP bypass Tor",
        status="warn",
        severity="low",
        evidence=(
            f"Conntrack montre {total} connexion(s) ESTABLISHED venant de "
            f"bridges Tor-routés mais ne passant PAS par TransPort:9040 "
            f"({', '.join(f'{b}={n}' for b, n in bad.items())}). "
            "Probablement des connexions ouvertes AVANT l'activation du "
            "transparent routing — elles continuent à sortir en clair "
            "jusqu'à leur fermeture."
        ),
        remediation=(
            "Flusher conntrack après l'apply : `conntrack -F` "
            "(ou redémarrer les clients du réseau)."
        ),
    )


# ── Orchestration ──────────────────────────────────────────────────


async def run_audit(
    ssh: SlateSSH,
    network_store: NetworkStore,
) -> TorAuditReport:
    install = await detect_install(ssh)
    networks = await network_store.list_all()

    transparent_nets = [n for n in networks if n.tor_route_mode == "transparent"]
    transparent_gws = [n.gateway_ip for n in transparent_nets if n.gateway_ip]

    findings: list[AuditFinding] = []

    if not install.tor:
        # Tor isn't installed at all — surface one explanatory finding and
        # skip every check (they'd all be noise).
        findings.append(AuditFinding(
            id="tor.installed",
            label="Tor non installé",
            status="info",
            severity="info",
            evidence="Le binaire tor n'est pas présent sur le Slate.",
            remediation="POST /api/tor/install (ou bouton « Installer » sur /networks/tor).",
        ))
        return TorAuditReport(
            score=100,
            findings=findings,
            tor_installed=False,
            tor_running=False,
            transparent_networks=[],
        )

    bindings = await _listening_bindings(ssh)
    # Anything listening on tor-typical ports means daemon up.
    tor_running = any(p in bindings for p in (9050, 9040, 9051, 5353))

    findings.extend(_check_port_bindings(bindings, transparent_gws))

    ip6 = await _ip6tables_forward(ssh)
    findings.append(_check_ipv6_leak(ip6, transparent_nets))

    kill = await _filter_chain(ssh, "SC_TOR_KILL")
    findings.append(_check_kill_switch(kill, transparent_nets))

    orphan_counts: dict[str, int | None] = {}
    for n in transparent_nets:
        # Filter by THIS network's CIDR — not bridge_name, not "10.*".
        # See `_conntrack_orphans_count` docstring for the historical bug.
        orphan_counts[n.bridge_name] = await _conntrack_orphans_count(
            ssh, n.subnet_cidr,
        )
    findings.append(_check_conntrack_orphans(orphan_counts))

    # Score = 100 - sum of penalties for fail/warn findings.
    score = 100
    for f in findings:
        if f.status in {"fail", "warn"}:
            score -= _PENALTY.get(f.severity, 0)
    score = max(0, score)

    return TorAuditReport(
        score=score,
        findings=findings,
        tor_installed=install.tor,
        tor_running=tor_running,
        transparent_networks=[n.slug for n in transparent_nets],
    )
