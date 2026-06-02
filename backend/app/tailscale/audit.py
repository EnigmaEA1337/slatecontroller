"""Tailscale security audit — local checks (no admin PAT required).

Mirrors the Security Device Status pattern: collect raw evidence in one
probe, then run each check independently with a severity → score penalty
aggregation. Phase B (PAT-backed tailnet-wide ACL/lock/SSO checks) is
intentionally left as a future extension; the current implementation can
fully audit a single device's posture from the Slate's `tailscale` CLI +
status JSON alone.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError
from app.tailscale.admin_api import TailscaleAdminAPI, TailscaleAdminAPIError
from app.tailscale.admin_store import TailscaleAdminStore
from app.tailscale.store import TailscaleStore

logger = structlog.get_logger(__name__)

Severity = Literal["critical", "high", "medium", "low", "info", "pass"]
CheckStatus = Literal["pass", "fail", "warn", "info", "skip"]

# Penalty applied to the 100-point base score for a FAILING/WARNING check.
# "pass" / "info" findings never deduct.
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
    evidence: str
    recommendation: str | None = None
    # True when the controller knows an idempotent fix for this exact
    # finding id AND its current status warrants action. Surfaced as a
    # "Corriger" button on the audit page ; non-fixable findings only
    # get the remediation text (PAT setup, ACL edits, etc.).
    fix_available: bool = False


# Finding ids the controller can auto-fix. Kept in sync with the
# /api/tailscale/audit/fix endpoint's dispatch table.
TAILSCALE_FIXABLE_IDS: frozenset[str] = frozenset({
    "daemon_running",     # start daemon (init.d + uci enable)
    "uci_enable",         # uci set tailscale enabled=1 for boot
    "shields_up",         # tailscale set --shields-up=true
})


@dataclass
class AuditReport:
    score: int  # 0..100
    grade: Literal["A", "B", "C", "D", "F"]
    pass_count: int
    fail_count: int
    warn_count: int
    findings: list[AuditFinding] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_summary: dict[str, Any] = field(default_factory=dict)


# Parse SC_FR_TS_ADMIN_DROP_ALL.dest_port to know which TCP ports the
# tailnet-admin firewall blocks for non-whitelisted peers. The handler
# at `slate_agent/scripts/handlers/tailscale.sh` writes the catch-all
# REJECT rule with a space-separated port list, e.g.
#   firewall.SC_FR_TS_ADMIN_DROP_ALL.dest_port='22 443 80 3000 8000'
# UCI values are single-quoted on stdout. Returns the set of ports as
# ints (empty when no firewall rules present → nothing protected).
_DEST_PORT_RE = re.compile(
    r"^firewall\.SC_FR_TS_ADMIN_DROP_ALL\.dest_port=['\"]?([^'\"\s]+(?:\s+[^'\"\s]+)*)['\"]?$",
    re.MULTILINE,
)
# Counts SC_FR_TS_ADMIN_ALLOW_* sections — one per whitelisted IP.
_ALLOW_SECTION_RE = re.compile(
    r"^firewall\.SC_FR_TS_ADMIN_ALLOW_[A-Z0-9_]+=rule$",
    re.MULTILINE,
)


def _parse_admin_fw_protected_ports(raw: str | None) -> set[int]:
    if not raw:
        return set()
    m = _DEST_PORT_RE.search(raw)
    if not m:
        return set()
    out: set[int] = set()
    for tok in m.group(1).split():
        try:
            out.add(int(tok))
        except ValueError:
            continue
    return out


def _count_admin_fw_allow_ips(raw: str | None) -> int:
    if not raw:
        return 0
    return len(_ALLOW_SECTION_RE.findall(raw))


# Ports we *intentionally* leave open on the tailnet. They serve tailnet
# peers (DNS, NTP, multicast discovery, Tailscale's own control plane)
# and aren't part of the admin surface. The listening_surface check
# excludes them from the "bare exposed" count so that a normally-
# configured Slate audits clean without forcing the operator to manually
# justify each one.
KNOWN_SERVICE_PORTS_TCP: frozenset[int] = frozenset({
    53,     # dnsmasq DNS over TCP — fallback for queries too large for UDP
            # (EDNS0 truncation, AXFR-like responses). Standard side-channel
            # to the UDP DNS service ; reachable on the tailnet but harmless.
    34641,  # Tailscale peerapi (internal, managed by tailscaled itself)
    3053,   # AdGuard Home DNS (forced port — default 53 conflicts with dnsmasq)
})
KNOWN_SERVICE_PORTS_UDP: frozenset[int] = frozenset({
    53,    # dnsmasq DNS for tailnet clients
    67,    # DHCP server (dnsmasq) — listens on all interfaces by default.
           # Reachable from the tailnet but functionally a no-op : tailnet
           # peers already get their IP from Tailscale's coordination
           # server, they don't DHCP from us. Could be tightened with
           # `no-dhcp-interface=tailscale0` in dnsmasq if pedantic, but
           # there's no actual leak.
    123,   # NTP — clock sync for tailnet clients
    853,   # DoT (DNS-over-TLS) for clients
    3053,  # AdGuard Home DNS (UDP side)
    5353,  # mDNS (avahi) for local discovery
    41641, # Tailscale's own UDP port (STUN / direct connections)
})


def _grade(score: int) -> Literal["A", "B", "C", "D", "F"]:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


class TailscaleAuditor:
    """Run the Tailscale security audit (local + optional cloud)."""

    def __init__(
        self,
        ssh: SlateSSH,
        store: TailscaleStore,
        admin_store: TailscaleAdminStore | None = None,
    ) -> None:
        self._ssh = ssh
        self._store = store
        self._admin_store = admin_store

    # ---- data collection -----------------------------------------------

    async def _collect(self) -> dict[str, Any]:
        """Single probe gathering every datapoint the checks need.

        Each sub-probe is best-effort: failures are stored as None so the
        relevant checks can degrade gracefully (skip → don't penalise).
        """
        async def probe(cmd: str, timeout: float = 15.0) -> str | None:
            try:
                r = await self._ssh.run(cmd, timeout=timeout)
                return r.stdout
            except SlateSSHError as exc:
                logger.warning("audit.probe_failed", cmd=cmd[:60], err=str(exc))
                return None

        # Parallelize independent probes — different SSH channels, ~3x faster.
        (
            version_raw,
            status_raw,
            prefs_raw,
            netcheck_raw,
            listening_raw,
            uci_raw,
            admin_fw_raw,
        ) = await asyncio.gather(
            probe("tailscale version 2>&1"),
            probe("tailscale status --json 2>&1"),
            probe("tailscale debug prefs 2>&1"),
            probe("tailscale netcheck --format=json 2>&1", timeout=25.0),
            # Listening TCP/UDP sockets; awk-grep tailscale0 by its IP.
            probe(
                # `ss` may not be packaged on the Slate; fall back to busybox netstat.
                # Output normalised: "<proto> <local_addr> <state>" one per line.
                "if command -v ss >/dev/null 2>&1; then "
                "ss -tulnH 2>/dev/null | awk '{print $1,$5,$2}' | sort -u; "
                "else "
                "netstat -tuln 2>/dev/null | awk 'NR>2 && $1 ~ /^(tcp|udp)/ {print $1,$4,$6}' | sort -u; "
                "fi"
            ),
            probe(
                "uci get tailscale.settings.enabled 2>&1; uci get tailscale.settings.flags 2>&1"
            ),
            # Tailnet admin firewall : the SC_FR_TS_ADMIN_* UCI rules pushed
            # by the tailscale handler when the whitelist is non-empty.
            # Used by `_check_listening_surface` to mark each listening
            # port as "filtered by firewall" instead of bare "exposed".
            probe("uci show firewall 2>/dev/null | grep -E '^firewall\\.SC_FR_TS_ADMIN_'"),
        )

        return {
            "version_raw": version_raw,
            "status_raw": status_raw,
            "prefs_raw": prefs_raw,
            "netcheck_raw": netcheck_raw,
            "listening_raw": listening_raw,
            "uci_raw": uci_raw,
            "admin_fw_raw": admin_fw_raw,
        }

    @staticmethod
    def _parse_status(raw: str | None) -> dict[str, Any]:
        if not raw or not raw.strip().startswith("{"):
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # Log the parse failure with a snippet so the operator can tell
            # whether the daemon returned an error string vs. malformed JSON.
            # Empty dict still returned so dependent checks degrade gracefully.
            logger.warning(
                "audit.parse_failed",
                source="tailscale_status",
                error=str(exc),
                sample=raw[:120],
            )
            return {}

    @staticmethod
    def _parse_netcheck(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        # `tailscale netcheck --format=json` prints one JSON object, possibly
        # preceded by a log line like "report:" — extract the first {...}.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            logger.warning(
                "audit.parse_failed",
                source="tailscale_netcheck",
                error="no JSON object found in output",
                sample=raw[:120],
            )
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            logger.warning(
                "audit.parse_failed",
                source="tailscale_netcheck",
                error=str(exc),
                sample=m.group(0)[:120],
            )
            return {}

    @staticmethod
    def _parse_prefs(raw: str | None) -> dict[str, Any]:
        """`tailscale debug prefs` outputs a pretty-printed JSON-ish blob.

        We attempt JSON, fall back to a couple of regexes for the fields
        we actually care about (ShieldsUp, ExitNodeID, ControlURL). The
        regex fallback is by design — older tailscale versions emit a
        non-JSON pretty format — so we *don't* warn when JSON fails; only
        when even the regex fallback finds nothing (corrupt input).
        """
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            out: dict[str, Any] = {}
            for key in (
                "ShieldsUp", "RouteAll", "CorpDNS", "ExitNodeID",
                "ExitNodeIP", "AdvertiseRoutes", "Hostname",
                "ControlURL", "WantRunning", "RunSSH", "AdvertiseTags",
            ):
                m = re.search(rf'"{key}":\s*(\S+?)(?:,|$)', raw)
                if m:
                    v = m.group(1).rstrip(",").strip()
                    if v in ("true", "false"):
                        out[key] = v == "true"
                    elif v == "null":
                        out[key] = None
                    else:
                        out[key] = v.strip('"')
            if not out:
                logger.warning(
                    "audit.parse_failed",
                    source="tailscale_prefs",
                    error="neither JSON nor regex fallback yielded any field",
                    sample=raw[:120],
                )
            return out

    # ---- individual checks ---------------------------------------------

    async def _check_daemon_running(
        self, status: dict[str, Any]
    ) -> AuditFinding:
        state = status.get("BackendState", "")
        if state == "Running":
            return AuditFinding(
                id="daemon_running",
                label="Daemon Tailscale opérationnel",
                status="pass", severity="pass",
                evidence=f"BackendState={state}",
            )
        return AuditFinding(
            id="daemon_running",
            label="Daemon Tailscale opérationnel",
            status="fail", severity="critical",
            evidence=f"BackendState={state or 'unknown'}",
            recommendation="Lancer le daemon: UCI enable + /etc/init.d/tailscale start. Sans daemon, aucune politique Tailscale n'est appliquée.",
        )

    async def _check_auth_key_stored(self) -> AuditFinding:
        meta = await self._store.get_metadata()
        if meta.get("has_auth_key"):
            return AuditFinding(
                id="auth_key_stored",
                label="Auth key stockée chiffrée at-rest",
                status="pass", severity="pass",
                evidence="auth key présente, chiffrement Fernet (app_secrets)",
            )
        return AuditFinding(
            id="auth_key_stored",
            label="Auth key stockée chiffrée at-rest",
            status="info", severity="info",
            evidence="aucune auth key persistée (mode browser-login)",
            recommendation="Pour un déploiement non-interactif (autoboot), utiliser une auth key réutilisable + tags.",
        )

    async def _check_device_tagged(
        self, status: dict[str, Any]
    ) -> AuditFinding:
        self_node = status.get("Self") or {}
        tags = self_node.get("Tags") or []
        if tags:
            return AuditFinding(
                id="device_tagged",
                label="Device tagué (ACL enforceable)",
                status="pass", severity="pass",
                evidence=f"tags={tags}",
            )
        return AuditFinding(
            id="device_tagged",
            label="Device tagué (ACL enforceable)",
            status="fail", severity="high",
            evidence="Self.Tags vide → l'identité est liée à un USER, pas à une machine.",
            recommendation=(
                "Régénérer une auth key avec --tags=tag:router (ou autre) "
                "dans admin.tailscale.com → Settings → Keys. Sans tag, "
                "les ACLs taggées ne s'appliquent PAS et le device hérite "
                "des droits de l'utilisateur propriétaire."
            ),
        )

    async def _check_magicdns(self, status: dict[str, Any]) -> AuditFinding:
        suffix = (
            status.get("MagicDNSSuffix")
            or (status.get("CurrentTailnet") or {}).get("MagicDNSSuffix")
            or ""
        )
        if suffix:
            return AuditFinding(
                id="magicdns_enabled",
                label="MagicDNS actif",
                status="pass", severity="pass",
                evidence=f"suffix={suffix}",
            )
        return AuditFinding(
            id="magicdns_enabled",
            label="MagicDNS actif",
            status="fail", severity="low",
            evidence="MagicDNSSuffix absent",
            recommendation="Activer MagicDNS dans admin Tailscale → DNS. Évite de dépendre d'IPs 100.x mouvantes.",
        )

    async def _check_shields_up(
        self, prefs: dict[str, Any]
    ) -> AuditFinding:
        shields = prefs.get("ShieldsUp")
        if shields is True:
            return AuditFinding(
                id="shields_up",
                label="Shields-up (drop trafic entrant tailnet)",
                status="pass", severity="pass",
                evidence="ShieldsUp=true",
            )
        if shields is False:
            return AuditFinding(
                id="shields_up",
                label="Shields-up (drop trafic entrant tailnet)",
                status="info", severity="info",
                evidence="ShieldsUp=false (le device accepte le trafic entrant des peers)",
                recommendation="Activer si ce Slate n'expose AUCUN service au tailnet. Sinon, gouverner via ACLs côté admin Tailscale.",
            )
        return AuditFinding(
            id="shields_up",
            label="Shields-up (drop trafic entrant tailnet)",
            status="skip", severity="info",
            evidence="prefs indisponible",
        )

    async def _check_advertise_routes_safe(
        self, status: dict[str, Any]
    ) -> AuditFinding:
        routes = (status.get("Self") or {}).get("PrimaryRoutes") or []
        # Heuristic: anything broader than /16 is suspicious (a /8 = 16M hosts),
        # 0.0.0.0/0 here means we're acting as exit-node legitimately ONLY if
        # ExitNodeOption is also True.
        bad: list[str] = []
        for r in routes:
            try:
                _, prefix_s = r.split("/")
                prefix = int(prefix_s)
            except ValueError:
                continue
            if prefix < 16 and r not in ("0.0.0.0/0", "::/0"):
                bad.append(r)
        if not bad:
            return AuditFinding(
                id="advertise_routes_safe",
                label="Routes annoncées non-leaky",
                status="pass", severity="pass",
                evidence=f"{len(routes)} route(s) annoncée(s), aucune <= /15",
            )
        return AuditFinding(
            id="advertise_routes_safe",
            label="Routes annoncées non-leaky",
            status="fail", severity="medium",
            evidence=f"routes très larges: {bad}",
            recommendation="Découper les subnets en /24 ou /23. Annoncer un /8 publie une vaste partie de l'espace privé au tailnet entier.",
        )

    async def _check_exit_node_consistency(
        self,
        status: dict[str, Any],
        store_meta: dict[str, Any] | None = None,
    ) -> AuditFinding:
        applied = ((store_meta or {}).get("config") or {})
        advertise_exit = bool(applied.get("advertise_exit_node"))
        exit_node_target = applied.get("exit_node") or ""

        # Pure publication mode — info only.
        if advertise_exit and not exit_node_target:
            return AuditFinding(
                id="exit_node_consistency",
                label="Cohérence exit-node",
                status="info", severity="info",
                evidence="Slate annoncé comme exit node (publication)",
                recommendation="Vérifier que la route 0.0.0.0/0 est *approuvée* dans admin.tailscale.com → machines → wraith-7 → Edit route settings, sinon les peers ne pourront pas l'utiliser.",
            )
        # Using a peer as exit — check that peer exists and is online.
        if exit_node_target:
            peers = status.get("Peer") or {}
            found_online = False
            for p in peers.values():
                if not isinstance(p, dict):
                    continue
                if exit_node_target in (
                    p.get("HostName") or "",
                    p.get("DNSName") or "",
                ) or exit_node_target in (p.get("TailscaleIPs") or []):
                    found_online = bool(p.get("Online"))
                    break
            if found_online:
                return AuditFinding(
                    id="exit_node_consistency",
                    label="Cohérence exit-node",
                    status="pass", severity="pass",
                    evidence=f"exit-node={exit_node_target} online",
                )
            return AuditFinding(
                id="exit_node_consistency",
                label="Cohérence exit-node",
                status="fail", severity="high",
                evidence=f"exit-node configuré={exit_node_target} introuvable/offline",
                recommendation="Le Slate sort sa default route via un peer indisponible → fuite réseau ou interruption. Configurer un fallback (HA watchdog) ou retirer l'exit-node.",
            )
        return AuditFinding(
            id="exit_node_consistency",
            label="Cohérence exit-node",
            status="info", severity="info",
            evidence="aucun exit-node configuré (sortie WAN locale)",
        )

    async def _check_stale_peers(
        self, status: dict[str, Any]
    ) -> AuditFinding:
        peers = status.get("Peer") or {}
        now = datetime.now(UTC)
        stale: list[str] = []
        for p in peers.values():
            if not isinstance(p, dict):
                continue
            ls = p.get("LastSeen")
            if not ls:
                continue
            try:
                dt = datetime.fromisoformat(str(ls).replace("Z", "+00:00"))
            except ValueError:
                continue
            # Skip never-seen (epoch).
            if dt.year < 2010:
                continue
            if (now - dt).days > 30 and not p.get("Online"):
                stale.append(p.get("HostName") or p.get("DNSName") or "?")
        if not stale:
            return AuditFinding(
                id="stale_peers",
                label="Pas de peers obsolètes (>30j)",
                status="pass", severity="pass",
                evidence=f"{len(peers)} peer(s), 0 stale",
            )
        return AuditFinding(
            id="stale_peers",
            label="Pas de peers obsolètes (>30j)",
            status="fail", severity="low",
            evidence=f"{len(stale)} stale: {stale[:5]}",
            recommendation="Retirer les devices abandonnés depuis admin.tailscale.com → machines. Réduit la surface ACL et les clés zombies.",
        )

    async def _check_netcheck_quality(
        self, netcheck: dict[str, Any]
    ) -> AuditFinding:
        if not netcheck:
            return AuditFinding(
                id="netcheck_quality",
                label="Connectivité réseau (UDP/DERP)",
                status="skip", severity="info",
                evidence="netcheck indisponible",
            )
        udp = netcheck.get("UDP")
        ipv4 = netcheck.get("GlobalV4") or ""
        if udp is False:
            return AuditFinding(
                id="netcheck_quality",
                label="Connectivité réseau (UDP/DERP)",
                status="fail", severity="low",
                evidence="UDP=false → connexions peer routées via DERP relay",
                recommendation="Vérifier qu'aucun firewall en amont ne bloque UDP/41641. Sans UDP direct, la latence est dégradée et la bande passante limitée par les relays.",
            )
        return AuditFinding(
            id="netcheck_quality",
            label="Connectivité réseau (UDP/DERP)",
            status="pass", severity="pass",
            evidence=f"UDP=ok ipv4={ipv4 or '?'}",
        )

    async def _check_uci_enable(
        self, uci_raw: str | None
    ) -> AuditFinding:
        if not uci_raw:
            return AuditFinding(
                id="uci_enable",
                label="UCI tailscale.settings.enabled=1",
                status="skip", severity="info",
                evidence="uci unreachable",
            )
        first_line = (uci_raw.strip().splitlines() or [""])[0].strip()
        if first_line == "1":
            return AuditFinding(
                id="uci_enable",
                label="UCI tailscale.settings.enabled=1",
                status="pass", severity="pass",
                evidence="GL.iNet init.d se relance correctement au boot",
            )
        return AuditFinding(
            id="uci_enable",
            label="UCI tailscale.settings.enabled=1",
            status="fail", severity="medium",
            evidence=f"valeur={first_line!r} → init.d no-op au reboot",
            recommendation="`uci set tailscale.settings.enabled=1 && uci commit tailscale` — sinon le daemon ne redémarre pas après un reboot du Slate.",
        )

    async def _check_listening_surface(
        self,
        listening_raw: str | None,
        status: dict[str, Any],
        prefs: dict[str, Any],
        admin_fw_raw: str | None = None,
    ) -> AuditFinding:
        if not listening_raw:
            return AuditFinding(
                id="listening_surface",
                label="Surface d'écoute sur tailscale0",
                status="skip", severity="info",
                evidence="ss indisponible",
            )
        ts_ips = (status.get("Self") or {}).get("TailscaleIPs") or []
        if not ts_ips:
            return AuditFinding(
                id="listening_surface",
                label="Surface d'écoute sur tailscale0",
                status="skip", severity="info",
                evidence="aucune IP Tailscale assignée",
            )
        # Match either an explicit bind on the Tailscale IP, or wildcard binds
        # which are also reachable from the tailnet.
        exposed: list[tuple[str, str, int]] = []  # (proto, addr, port)
        for line in listening_raw.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            proto = parts[0]
            addr = parts[1]
            wildcard = addr.startswith(("0.0.0.0:", "*:", "[::]:", ":::"))
            on_ts = any(addr.startswith(ip + ":") or addr.startswith("[" + ip + "]:") for ip in ts_ips)
            if not (wildcard or on_ts):
                continue
            port_s = addr.rsplit(":", 1)[-1]
            try:
                port_n = int(port_s)
            except ValueError:
                continue
            exposed.append((proto, addr, port_n))

        # Parse the SC_FR_TS_ADMIN_* rules — the handler pushes one
        # ACCEPT rule per whitelisted source IP, plus one REJECT catch-
        # all on the tailnet CIDR. We only need the catch-all to know
        # which TCP ports are filtered for non-whitelisted peers ;
        # ACCEPT rules let the whitelisted peers through but don't
        # change the surface "what's reachable to a random peer".
        protected_tcp: set[int] = _parse_admin_fw_protected_ports(admin_fw_raw)
        whitelist_count: int = _count_admin_fw_allow_ips(admin_fw_raw)

        shields = prefs.get("ShieldsUp") is True
        if not exposed:
            return AuditFinding(
                id="listening_surface",
                label="Surface d'écoute sur tailscale0",
                status="pass", severity="pass",
                evidence="aucun service en écoute sur le tailnet",
            )
        if shields:
            return AuditFinding(
                id="listening_surface",
                label="Surface d'écoute sur tailscale0",
                status="info", severity="info",
                evidence=f"{len(exposed)} port(s) en écoute mais ShieldsUp=true (drop)",
            )

        # Three-way split :
        #   - filtered : TCP listener on a port covered by SC_FR_TS_ADMIN_DROP_ALL
        #   - services : listener on a port we intentionally leave open
        #     (Tailscale peerapi, DNS, NTP, mDNS — cf. KNOWN_SERVICE_PORTS_*),
        #     OR UDP on an ephemeral high port (>32768) which is typically
        #     a transient client-side binding (DHCP client, ICMPv6, DNS
        #     resolver source port, mdnsd reply socket, …), not an
        #     intentional admin endpoint
        #   - bare     : the rest = actual unexpected exposure
        bare: list[str] = []
        filtered: list[str] = []
        services: list[str] = []
        for proto, addr, port in exposed:
            label = f"{proto} {addr}"
            if proto == "tcp" and port in protected_tcp:
                filtered.append(label)
            elif proto == "tcp" and port in KNOWN_SERVICE_PORTS_TCP:
                services.append(label)
            elif proto == "udp" and port in KNOWN_SERVICE_PORTS_UDP:
                services.append(label)
            elif proto == "udp" and port >= 32768:
                # IANA ephemeral / Linux dynamic range. Random bindings
                # used by client-side connections, not admin surface.
                services.append(label)
            else:
                bare.append(label)

        if not bare:
            # Every listening port is either firewall-protected or a
            # known service. That's the desired posture.
            parts: list[str] = []
            if filtered:
                parts.append(
                    f"{len(filtered)} filtré(s) par SC_FR_TS_ADMIN_DROP_ALL "
                    f"({whitelist_count} IP(s) whitelistée(s))"
                )
            if services:
                parts.append(f"{len(services)} service(s) tailnet légitimes (DNS/NTP/mDNS/peerapi)")
            return AuditFinding(
                id="listening_surface",
                label="Surface d'écoute sur tailscale0",
                status="pass", severity="pass",
                evidence=f"Posture saine : {' · '.join(parts) or 'aucun listener'}.",
            )

        if filtered or services:
            # Mixed : firewall + known services cover some ports, but
            # not all — there are genuinely unexpected listeners.
            extras: list[str] = []
            if filtered:
                extras.append(f"{len(filtered)} filtré(s) par SC_FR_TS_ADMIN_DROP_ALL")
            if services:
                extras.append(f"{len(services)} service(s) tailnet légitimes")
            return AuditFinding(
                id="listening_surface",
                label="Surface d'écoute sur tailscale0",
                status="warn", severity="medium",
                evidence=(
                    f"{len(bare)} port(s) inattendu(s) accessibles : {bare[:8]}. "
                    f"({' · '.join(extras)}.)"
                ),
                recommendation=(
                    "Ajouter les ports manquants à ADMIN_PORTS_TCP si ce sont "
                    "des admin endpoints, ou à KNOWN_SERVICE_PORTS_TCP/UDP "
                    "si ce sont des services tailnet légitimes."
                ),
            )

        # No firewall rules at all — the original warn.
        return AuditFinding(
            id="listening_surface",
            label="Surface d'écoute sur tailscale0",
            status="warn", severity="medium",
            evidence=f"{len(bare)} port(s) accessibles au tailnet: {bare[:8]}",
            recommendation=(
                "Soit activer Shields-up, soit configurer la whitelist Tailnet admin "
                "(Settings → Tailnet admin) pour pousser les règles SC_FR_TS_ADMIN_*."
            ),
        )

    async def _check_version_recency(
        self, version_raw: str | None
    ) -> AuditFinding:
        if not version_raw:
            return AuditFinding(
                id="version_recency",
                label="Version Tailscale récente",
                status="skip", severity="info",
                evidence="version inconnue",
            )
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", version_raw)
        if not m:
            return AuditFinding(
                id="version_recency",
                label="Version Tailscale récente",
                status="skip", severity="info",
                evidence=f"version non parseable: {version_raw[:80]!r}",
            )
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ver_str = f"{major}.{minor}.{patch}"
        # Baseline: 1.60 (early 2024). Anything older is starting to lag
        # on netfilter/SSO compat. >= 1.70 ideal.
        if (major, minor) >= (1, 70):
            return AuditFinding(
                id="version_recency",
                label="Version Tailscale récente",
                status="pass", severity="pass",
                evidence=f"v{ver_str}",
            )
        if (major, minor) >= (1, 60):
            return AuditFinding(
                id="version_recency",
                label="Version Tailscale récente",
                status="info", severity="info",
                evidence=f"v{ver_str} (récente mais en retard sur le canal stable)",
            )
        return AuditFinding(
            id="version_recency",
            label="Version Tailscale récente",
            status="fail", severity="medium",
            evidence=f"v{ver_str} — antérieure à 1.60",
            recommendation="opkg update && opkg upgrade gl-sdk4-tailscale, ou attendre l'OTA GL.iNet.",
        )

    async def _check_key_expiry(
        self, status: dict[str, Any]
    ) -> AuditFinding:
        self_node = status.get("Self") or {}
        key_exp = self_node.get("KeyExpiry")
        if not key_exp:
            return AuditFinding(
                id="key_expiry",
                label="Expiration clé device",
                status="info", severity="info",
                evidence="pas d'expiration (key disabled = ok pour routeur dédié)",
                recommendation="Pour un device long-lived (routeur), désactiver l'expiration côté admin Tailscale (machines → Disable key expiry).",
            )
        try:
            exp = datetime.fromisoformat(str(key_exp).replace("Z", "+00:00"))
        except ValueError:
            return AuditFinding(
                id="key_expiry",
                label="Expiration clé device",
                status="skip", severity="info",
                evidence=f"date non parseable: {key_exp}",
            )
        days = (exp - datetime.now(UTC)).days
        if days < 0:
            return AuditFinding(
                id="key_expiry",
                label="Expiration clé device",
                status="fail", severity="high",
                evidence=f"clé expirée depuis {-days}j",
                recommendation="Reconnect avec une nouvelle auth key.",
            )
        if days < 7:
            return AuditFinding(
                id="key_expiry",
                label="Expiration clé device",
                status="warn", severity="high",
                evidence=f"clé expire dans {days}j",
                recommendation="Reconnect avec nouvelle key OU désactiver expiry sur ce device (admin.tailscale.com).",
            )
        if days < 30:
            return AuditFinding(
                id="key_expiry",
                label="Expiration clé device",
                status="warn", severity="low",
                evidence=f"expire dans {days}j",
            )
        return AuditFinding(
            id="key_expiry",
            label="Expiration clé device",
            status="pass", severity="pass",
            evidence=f"expire dans {days}j",
        )

    # ---- cloud checks (admin API) --------------------------------------

    async def _cloud_checks(
        self, api: TailscaleAdminAPI
    ) -> list[AuditFinding]:
        """Run all tailnet-wide checks against the admin API.

        We collect every resource first (5 parallel requests, ~500ms each)
        then derive findings — sharing the same payload across multiple
        checks (e.g. devices list feeds both untagged & stale checks).
        """
        async def safe(coro):
            try:
                return await coro
            except TailscaleAdminAPIError as exc:
                logger.warning("audit.cloud.probe_failed", err=str(exc))
                return None

        acl, devices, keys, settings, dns_pref = await asyncio.gather(
            safe(api.acl()),
            safe(api.devices()),
            safe(api.keys()),
            safe(api.settings()),
            safe(api.dns_preferences()),
        )

        out: list[AuditFinding] = []
        out.append(self._cloud_check_acl_default_deny(acl))
        out.append(self._cloud_check_acl_tag_owners(acl))
        out.append(self._cloud_check_untagged_devices(devices))
        out.append(self._cloud_check_stale_devices(devices))
        out.append(self._cloud_check_auth_keys_hygiene(keys))
        out.append(self._cloud_check_device_approval(settings))
        out.append(self._cloud_check_key_duration(settings))
        out.append(self._cloud_check_https_certs(dns_pref))
        return out

    def _cloud_check_acl_default_deny(
        self, acl: dict[str, Any] | None
    ) -> AuditFinding:
        if acl is None:
            return AuditFinding(
                id="cloud_acl_default_deny",
                label="ACL: default-deny appliqué",
                status="skip", severity="info",
                evidence="ACL inaccessible (PAT 403 ou réseau).",
            )
        rules = acl.get("acls") or []
        # Flag an ACL that lets EVERYONE talk to EVERYONE — common default
        # but a security smell once you have services on the tailnet.
        wide_open = any(
            "*" in (r.get("src") or []) and "*:*" in (r.get("dst") or [])
            for r in rules if isinstance(r, dict)
        )
        if wide_open:
            return AuditFinding(
                id="cloud_acl_default_deny",
                label="ACL: default-deny appliqué",
                status="fail", severity="high",
                evidence="rule `*:*` détectée — tout le tailnet a accès à tout.",
                recommendation=(
                    "Restreindre par tags/users dans la policy ACL "
                    "(admin.tailscale.com → Access Controls). "
                    "Ex: `\"src\": [\"tag:admin\"], \"dst\": [\"tag:router:22\"]`."
                ),
            )
        return AuditFinding(
            id="cloud_acl_default_deny",
            label="ACL: default-deny appliqué",
            status="pass", severity="pass",
            evidence=f"{len(rules)} règle(s), pas de wildcard `*:*` détecté",
        )

    def _cloud_check_acl_tag_owners(
        self, acl: dict[str, Any] | None
    ) -> AuditFinding:
        if acl is None:
            return AuditFinding(
                id="cloud_acl_tag_owners",
                label="ACL: tagOwners défini",
                status="skip", severity="info",
                evidence="ACL inaccessible",
            )
        tag_owners = acl.get("tagOwners") or {}
        if tag_owners:
            return AuditFinding(
                id="cloud_acl_tag_owners",
                label="ACL: tagOwners défini",
                status="pass", severity="pass",
                evidence=f"{len(tag_owners)} tag(s) avec owners",
            )
        return AuditFinding(
            id="cloud_acl_tag_owners",
            label="ACL: tagOwners défini",
            status="fail", severity="medium",
            evidence="aucun bloc tagOwners — les devices taggés sont owned-by-user, pas par ACL",
            recommendation="Ajouter `\"tagOwners\": { \"tag:router\": [\"autogroup:admin\"] }` dans la policy.",
        )

    def _cloud_check_untagged_devices(
        self, devices: list[dict[str, Any]] | None
    ) -> AuditFinding:
        if devices is None:
            return AuditFinding(
                id="cloud_untagged_devices",
                label="Devices tagués (tailnet)",
                status="skip", severity="info",
                evidence="liste devices inaccessible",
            )
        untagged: list[str] = []
        for d in devices:
            tags = d.get("tags") or []
            if not tags:
                untagged.append(d.get("hostname") or d.get("name", "?"))
        if not untagged:
            return AuditFinding(
                id="cloud_untagged_devices",
                label="Devices tagués (tailnet)",
                status="pass", severity="pass",
                evidence=f"100% des {len(devices)} device(s) taggés",
            )
        return AuditFinding(
            id="cloud_untagged_devices",
            label="Devices tagués (tailnet)",
            status="fail", severity="medium",
            evidence=f"{len(untagged)}/{len(devices)} device(s) sans tag: {untagged[:5]}",
            recommendation=(
                "Les devices non-taggés héritent des droits de leur user-owner. "
                "Reconnect chaque device avec une auth key tagguée, ou éditer "
                "les tags depuis admin.tailscale.com → machines → Edit ACL tags."
            ),
        )

    def _cloud_check_stale_devices(
        self, devices: list[dict[str, Any]] | None
    ) -> AuditFinding:
        if devices is None:
            return AuditFinding(
                id="cloud_stale_devices",
                label="Devices actifs (<60j)",
                status="skip", severity="info",
                evidence="liste devices inaccessible",
            )
        now = datetime.now(UTC)
        stale: list[str] = []
        for d in devices:
            last = d.get("lastSeen")
            if not last:
                continue
            try:
                dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            except ValueError:
                continue
            if (now - dt).days > 60:
                stale.append(d.get("hostname") or d.get("name", "?"))
        if not stale:
            return AuditFinding(
                id="cloud_stale_devices",
                label="Devices actifs (<60j)",
                status="pass", severity="pass",
                evidence=f"0 device dormant sur {len(devices)}",
            )
        return AuditFinding(
            id="cloud_stale_devices",
            label="Devices actifs (<60j)",
            status="fail", severity="low",
            evidence=f"{len(stale)} device(s) inactif(s) >60j: {stale[:5]}",
            recommendation="Supprimer les devices obsolètes depuis admin.tailscale.com → machines.",
        )

    def _cloud_check_auth_keys_hygiene(
        self, keys: list[dict[str, Any]] | None
    ) -> AuditFinding:
        if keys is None or keys == []:
            return AuditFinding(
                id="cloud_auth_keys_hygiene",
                label="Hygiène auth keys",
                status="skip", severity="info",
                evidence="aucune key listée (ou PAT sans scope keys:read)",
            )
        no_expiry: list[str] = []
        old: list[str] = []
        now = datetime.now(UTC)
        for k in keys:
            kid = k.get("id", "?")[:10]
            exp = k.get("expires")
            if not exp:
                no_expiry.append(kid)
            else:
                try:
                    dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                    if (dt - now).days > 180:
                        old.append(kid)
                except ValueError:
                    pass
        issues: list[str] = []
        sev: Severity = "pass"
        if no_expiry:
            issues.append(f"{len(no_expiry)} sans expiry")
            sev = "high"
        if old:
            issues.append(f"{len(old)} expirent dans >180j")
            if sev != "high":
                sev = "low"
        if not issues:
            return AuditFinding(
                id="cloud_auth_keys_hygiene",
                label="Hygiène auth keys",
                status="pass", severity="pass",
                evidence=f"{len(keys)} key(s) actives, toutes avec expiry <180j",
            )
        return AuditFinding(
            id="cloud_auth_keys_hygiene",
            label="Hygiène auth keys",
            status="fail", severity=sev,
            evidence=f"{len(keys)} key(s) actives; {' / '.join(issues)}",
            recommendation="Auth keys = passwords longue-durée. Régénérer périodiquement (90j), expirer celles non utilisées (admin.tailscale.com → Keys).",
        )

    def _cloud_check_device_approval(
        self, settings: dict[str, Any] | None
    ) -> AuditFinding:
        if not settings:
            return AuditFinding(
                id="cloud_device_approval",
                label="Approbation device requise",
                status="skip", severity="info",
                evidence="settings inaccessible",
            )
        approval = settings.get("devicesApprovalOn")
        if approval is True:
            return AuditFinding(
                id="cloud_device_approval",
                label="Approbation device requise",
                status="pass", severity="pass",
                evidence="nouveau device doit être approuvé manuellement",
            )
        if approval is False:
            return AuditFinding(
                id="cloud_device_approval",
                label="Approbation device requise",
                status="fail", severity="medium",
                evidence="approbation désactivée — tout device avec auth key valide rejoint",
                recommendation="admin.tailscale.com → Settings → General → 'Device approval'. Ajoute une friction utile contre les auth keys volées.",
            )
        return AuditFinding(
            id="cloud_device_approval",
            label="Approbation device requise",
            status="skip", severity="info",
            evidence="champ devicesApprovalOn absent de la réponse",
        )

    def _cloud_check_key_duration(
        self, settings: dict[str, Any] | None
    ) -> AuditFinding:
        if not settings:
            return AuditFinding(
                id="cloud_key_duration",
                label="Durée max des keys",
                status="skip", severity="info",
                evidence="settings inaccessible",
            )
        # devicesKeyDurationDays / authorizationKeyDurationDays — naming varies.
        candidates = [
            "devicesKeyDurationDays",
            "authorizationKeyDurationDays",
            "networkFlowLoggingOn",  # not key-related but useful sanity check.
        ]
        for k in ("devicesKeyDurationDays", "authorizationKeyDurationDays"):
            if k in settings:
                d = settings[k]
                if isinstance(d, int):
                    if d > 365 or d == 0:
                        return AuditFinding(
                            id="cloud_key_duration",
                            label="Durée max des keys",
                            status="warn", severity="low",
                            evidence=f"{k}={d} — keys longue-durée",
                            recommendation="Réduire à 90 jours pour forcer une rotation périodique (Settings → Device key expiration).",
                        )
                    return AuditFinding(
                        id="cloud_key_duration",
                        label="Durée max des keys",
                        status="pass", severity="pass",
                        evidence=f"{k}={d} jours",
                    )
        return AuditFinding(
            id="cloud_key_duration",
            label="Durée max des keys",
            status="skip", severity="info",
            evidence=f"clé durée non exposée (fields={list(settings.keys())[:6]})",
        )

    def _cloud_check_https_certs(
        self, dns_pref: dict[str, Any] | None
    ) -> AuditFinding:
        if not dns_pref:
            return AuditFinding(
                id="cloud_https_certs",
                label="MagicDNS + HTTPS certs",
                status="skip", severity="info",
                evidence="dns/preferences inaccessible",
            )
        magic = dns_pref.get("magicDNS")
        if magic is True:
            return AuditFinding(
                id="cloud_https_certs",
                label="MagicDNS + HTTPS certs",
                status="pass", severity="pass",
                evidence="MagicDNS activé côté tailnet",
            )
        if magic is False:
            return AuditFinding(
                id="cloud_https_certs",
                label="MagicDNS + HTTPS certs",
                status="fail", severity="low",
                evidence="MagicDNS désactivé côté tailnet",
                recommendation="Activer (admin → DNS → MagicDNS) pour bénéficier des noms .ts.net + certificats HTTPS Let's Encrypt automatiques.",
            )
        return AuditFinding(
            id="cloud_https_certs",
            label="MagicDNS + HTTPS certs",
            status="skip", severity="info",
            evidence="magicDNS field absent",
        )

    # ---- orchestrator --------------------------------------------------

    async def run(self) -> AuditReport:
        raw = await self._collect()
        status_json = self._parse_status(raw.get("status_raw"))
        prefs_json = self._parse_prefs(raw.get("prefs_raw"))
        netcheck_json = self._parse_netcheck(raw.get("netcheck_raw"))
        store_meta = await self._store.get_metadata()

        findings: list[AuditFinding] = list(await asyncio.gather(
            self._check_daemon_running(status_json),
            self._check_auth_key_stored(),
            self._check_device_tagged(status_json),
            self._check_magicdns(status_json),
            self._check_shields_up(prefs_json),
            self._check_advertise_routes_safe(status_json),
            self._check_exit_node_consistency(status_json, store_meta),
            self._check_stale_peers(status_json),
            self._check_netcheck_quality(netcheck_json),
            self._check_uci_enable(raw.get("uci_raw")),
            self._check_listening_surface(
                raw.get("listening_raw"), status_json, prefs_json,
                admin_fw_raw=raw.get("admin_fw_raw"),
            ),
            self._check_version_recency(raw.get("version_raw")),
            self._check_key_expiry(status_json),
        ))

        # Cloud audit if a PAT is configured. Failures here add "skip" findings
        # without penalty — the user shouldn't be punished by network blips.
        cloud_enabled = False
        if self._admin_store is not None:
            pat = await self._admin_store.get_pat()
            if pat:
                cloud_enabled = True
                meta = await self._admin_store.get_metadata()
                tailnet_for_api = meta.get("tailnet") or "-"
                try:
                    async with TailscaleAdminAPI(pat, tailnet_for_api) as api:
                        findings.extend(await self._cloud_checks(api))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("audit.cloud.failed", err=str(exc))
                    findings.append(AuditFinding(
                        id="cloud_api_reachable",
                        label="API admin Tailscale joignable",
                        status="fail", severity="medium",
                        evidence=str(exc)[:200],
                        recommendation="Vérifier la validité du PAT (admin.tailscale.com → Settings → Keys) et la connectivité sortante vers api.tailscale.com:443.",
                    ))

        score = 100
        pass_n = fail_n = warn_n = 0
        for f in findings:
            if f.status == "pass":
                pass_n += 1
            elif f.status == "fail":
                fail_n += 1
                score -= _PENALTY[f.severity]
            elif f.status == "warn":
                warn_n += 1
                score -= _PENALTY[f.severity]

        score = max(0, score)
        # Stamp each finding with fix_available so the UI knows when to
        # show the "Corriger" button. Cheap to compute here (one set
        # membership check per finding) and avoids leaking the dispatch
        # map into the route layer.
        for f in findings:
            if f.id in TAILSCALE_FIXABLE_IDS and f.status in {"fail", "warn"}:
                f.fix_available = True
        return AuditReport(
            score=score,
            grade=_grade(score),
            pass_count=pass_n,
            fail_count=fail_n,
            warn_count=warn_n,
            findings=findings,
            raw_summary={
                "version": (raw.get("version_raw") or "").strip()[:120],
                "peers": len(status_json.get("Peer") or {}),
                "self_ip": (status_json.get("Self") or {}).get("TailscaleIPs", []),
                "tailnet": status_json.get("MagicDNSSuffix") or "",
                "cloud_enabled": cloud_enabled,
            },
        )
