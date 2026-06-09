"""Tailscale subnet routing (inverse) — per-(source LAN, destination CIDR).

Reconciles the Slate firewall so each LAN can reach exactly the tailnet
subnets the operator has approved for it, with the NAT mode chosen
per-destination.

The state lives on `NetworkRow.tailnet_destinations` (a list of
`{cidr, mode}` dicts). On apply, we walk every Network in the catalog
and translate the union of all desired pairs into iptables rules :

  • For every desired pair `(src_lan_cidr, dest_cidr, mode)` :
      - `iptables -A forwarding_<srczone>_rule -d <dest_cidr> -o
        tailscale0 -j ACCEPT` so the packet is allowed across the zones
      - if `mode == "snat"` : `iptables -t nat -I POSTROUTING -s
        <src_lan_cidr> -d <dest_cidr> -o tailscale0 -j SNAT
        --to-source <slate-ts-ip> -m comment --comment
        SC_FR_TS_SNAT_<zone>_<destslug>`

  • All rules tagged with the slate-controller comment prefix get
    flushed first so re-applies are idempotent.

  • The persistence layer (so the rules survive a `firewall reload`) is
    a balised block in `/etc/firewall.user` which `fw3` re-runs at the
    end of each reload.

Naming :
  - SNAT comment tag : `SC_FR_TS_SNAT_<UPPERZONE>_<DESTSLUG>`
    where DESTSLUG is the destination CIDR with `.` and `/` replaced by
    `_` (e.g. `10_13_69_0_24`).
  - Forwarding ACCEPT comment tag :
    `SC_FR_TS_FWD_<UPPERZONE>_<DESTSLUG>` — used by the flush step.
  - firewall.user block delimiters identical to the previous module
    (`### SC_FR_TS_SNAT block - managed by slate-controller`).

Compared to the previous coarse-grained API (`{zone -> mode}`), this
module takes a richer input :

    desired = {
        "<source_zone>": [
            (src_lan_cidr, dest_cidr, mode),
            ...
        ],
        ...
    }

The caller (`api/routes/networks.py` after a save, or a dedicated
endpoint) is responsible for translating the high-level
`tailnet_destinations` lists into this normalized shape.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Literal

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


TS_ZONE_NAME = "tailscale0"
FWD_TAG_PREFIX = "SC_FR_TS_FWD_"
SNAT_TAG_PREFIX = "SC_FR_TS_SNAT_"
# Tor uses a different rule shape : DNAT REDIRECT in PREROUTING instead
# of a FORWARD+SNAT pair. We tag those with their own prefix so the
# flush + persistence steps know what to look for.
TOR_TAG_PREFIX = "SC_FR_TS_TOR_"

# Tor daemon listening ports — match the OpenWrt/GL.iNet defaults baked
# into the `tor` UCI config. `TransPort` is the transparent-proxy TCP
# port ; `DNSPort` resolves through the network so we don't leak DNS to
# the WAN resolver.
TOR_TRANS_PORT = 9040
TOR_DNS_PORT = 9053

BLOCK_BEGIN = (
    "### SC_FR_TS_SNAT block - managed by slate-controller - do not edit ###"
)
BLOCK_END = "### SC_FR_TS_SNAT block end ###"

NatMode = Literal["routed", "snat"]
# Egress paths supported by the reconciler.
#   "tailnet" — fully implemented : forward via tailscale0, SNAT via
#               tailscale0's IPv4 when mode='snat'
#   "wan"     — fully implemented : forward via the resolved WAN
#               interface, SNAT via MASQUERADE so conntrack picks the
#               outbound IP (the box may be on a DHCP WAN that changes)
#   "proton" / "tor" — not yet implemented ; reconcile raises a clear
#                       error if any rule asks for them.
Via = Literal["tailnet", "wan", "proton", "tor"]


@dataclass(frozen=True)
class DesiredRule:
    """One (src LAN, dest CIDR, mode, via) tuple — the unit the firewall
    reconciler consumes."""

    src_zone: str       # firewall zone name (e.g. "nexus")
    src_cidr: str       # source LAN CIDR (for the SNAT rule selector)
    dest_cidr: str      # destination subnet to reach
    mode: NatMode
    via: Via = "tailnet"


@dataclass(frozen=True)
class LocalSubnet:
    """One L3-managed local network on the Slate, surfaced to the UI."""

    slug: str
    zone: str
    iface: str
    cidr: str
    ipaddr: str


@dataclass(frozen=True)
class ForwardingSnapshot:
    """Live state read from the Slate. The per-pair active rules are
    derived from iptables comment tags."""

    subnets: list[LocalSubnet] = field(default_factory=list)
    tailscale_zone_exists: bool = False
    tailscale_self_ip: str | None = None
    # WAN egress interface — needed by the reconciler when a rule asks
    # for via='wan'. None if the WAN can't be detected.
    wan_iface: str | None = None
    # Proton VPN tunnel interface (if any). None when Proton is not set
    # up. We accept any name that starts with `proton`, `wgproton`, or
    # is a WireGuard `wg*` iface — the actual name depends on the user's
    # Proton config (UCI `wireguard_proton`, manual `proton0`, etc.).
    proton_iface: str | None = None
    # True when a running Tor daemon listens on the expected TransPort.
    # Used to validate via='tor' rules before applying them.
    tor_active: bool = False
    # set of (zone, dest_cidr) pairs currently SNAT'd
    active_snat: set[tuple[str, str]] = field(default_factory=set)
    # set of (zone, dest_cidr) pairs currently FORWARD'd (routed or snat)
    active_fwd: set[tuple[str, str]] = field(default_factory=set)
    # set of (zone, dest_cidr) pairs currently TOR-REDIRECT'd
    active_tor: set[tuple[str, str]] = field(default_factory=set)


def _dest_slug(cidr: str) -> str:
    """Make a CIDR safe for use inside an iptables comment + UCI name."""
    return cidr.replace("/", "_").replace(".", "_")


def _fwd_tag(zone: str, cidr: str) -> str:
    return f"{FWD_TAG_PREFIX}{zone.upper()}_{_dest_slug(cidr)}"


def _snat_tag(zone: str, cidr: str) -> str:
    return f"{SNAT_TAG_PREFIX}{zone.upper()}_{_dest_slug(cidr)}"


def _tor_tag(zone: str, cidr: str, proto: str) -> str:
    return f"{TOR_TAG_PREFIX}{zone.upper()}_{_dest_slug(cidr)}_{proto.upper()}"


# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------

async def discover_state(ssh: SlateSSH) -> ForwardingSnapshot:
    """Read the live firewall + network UCI + iptables state."""
    cmd = (
        "echo '---NETWORKS---'; "
        "uci show network | grep -E '^network\\.[a-zA-Z0-9_-]+\\.(ipaddr|netmask|device|proto)='; "
        "echo '---ZONES---'; "
        "uci show firewall | grep -E '^firewall\\.[a-zA-Z0-9_-]+\\.(name|network)='; "
        "echo '---TS_ZONE---'; "
        f"uci show firewall.{TS_ZONE_NAME} 2>/dev/null || true; "
        "echo '---TS_IP---'; "
        "ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}'; "
        "echo '---WAN_IFACE---'; "
        # Resolve the default route's outgoing iface — this catches both
        # eth0 (wired WAN) and apcli0 (uplink Wi-Fi when the Slate is a
        # tethered client). Fallback to the UCI `network.wan.device`
        # when no default route is present.
        "ip -4 route show default 2>/dev/null | awk '{print $5}' | head -1 "
        "|| uci -q get network.wan.device || true; "
        "echo '---PROTON_IFACE---'; "
        # Any WireGuard / Proton-tagged interface. We grep the L3 set so
        # an iface with no IPv4 (down) doesn't get reported.
        "ip -4 -o addr show 2>/dev/null "
        "| awk '{print $2}' "
        "| grep -E '^(proton[0-9]*|wgproton[0-9]*|wg-proton|wg[0-9]+)$' "
        "| head -1; "
        "echo '---TOR_ACTIVE---'; "
        # Tor daemon test : a real `tor` process (excluding lookups via "
        # pgrep self-match), and TransPort actually listening.
        "pgrep -x tor >/dev/null && "
        f"netstat -tln 2>/dev/null | grep -q ':{TOR_TRANS_PORT} ' "
        "&& echo yes || echo no; "
        "echo '---SNAT_TAGS---'; "
        f"iptables -t nat -S POSTROUTING 2>/dev/null "
        f"| grep -E -- '--comment {SNAT_TAG_PREFIX}' || true; "
        "echo '---FWD_TAGS---'; "
        f"iptables -S 2>/dev/null "
        f"| grep -E -- '--comment {FWD_TAG_PREFIX}' || true; "
        "echo '---TOR_TAGS---'; "
        f"iptables -t nat -S PREROUTING 2>/dev/null "
        f"| grep -E -- '--comment {TOR_TAG_PREFIX}' || true"
    )
    try:
        r = await ssh.run(cmd, timeout=20)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH discover failed: {exc}") from exc
    parsed = _parse_uci_dump(r.stdout)
    (
        nets, zones, ts_ip, wan_iface, proton_iface, tor_active,
        snat_tags, fwd_tags, tor_tags,
    ) = parsed
    ts_zone_exists = TS_ZONE_NAME in zones

    subnets: list[LocalSubnet] = []
    for slug, kv in nets.items():
        if kv.get("proto") != "static":
            continue
        ipaddr = kv.get("ipaddr")
        netmask = kv.get("netmask")
        iface = kv.get("device", "")
        if not ipaddr or not netmask:
            continue
        cidr = _ip_netmask_to_cidr(ipaddr, netmask)
        zone = slug if slug in zones else _zone_for_network(zones, slug)
        if not zone:
            continue
        subnets.append(
            LocalSubnet(
                slug=slug, zone=zone, iface=iface, cidr=cidr,
                ipaddr=ipaddr,
            )
        )
    subnets.sort(key=lambda s: s.slug)

    # Decode tags into (zone, dest_cidr) pairs.
    active_snat = _tags_to_pairs(snat_tags, SNAT_TAG_PREFIX, subnets)
    active_fwd = _tags_to_pairs(fwd_tags, FWD_TAG_PREFIX, subnets)
    # Tor tags include a trailing _TCP/_UDP suffix — strip it so the
    # (zone, dest_cidr) pair shape stays uniform with the other sets.
    active_tor: set[tuple[str, str]] = set()
    for t in tor_tags:
        for suffix in ("_TCP", "_UDP"):
            if t.endswith(suffix):
                base = t[: -len(suffix)]
                pair = _decode_tag(base, TOR_TAG_PREFIX, subnets)
                if pair:
                    active_tor.add(pair)
                break

    return ForwardingSnapshot(
        subnets=subnets,
        tailscale_zone_exists=ts_zone_exists,
        tailscale_self_ip=ts_ip,
        wan_iface=wan_iface,
        proton_iface=proton_iface,
        tor_active=tor_active,
        active_snat=active_snat,
        active_fwd=active_fwd,
        active_tor=active_tor,
    )


def _tags_to_pairs(
    tags: set[str],
    prefix: str,
    subnets: list[LocalSubnet],
) -> set[tuple[str, str]]:
    """Reverse the `<PREFIX><UPPERZONE>_<DESTSLUG>` encoding into the
    canonical pairs (zone, dest_cidr)."""
    out: set[tuple[str, str]] = set()
    for tag in tags:
        pair = _decode_tag(tag, prefix, subnets)
        if pair:
            out.add(pair)
    return out


def _decode_tag(
    tag: str, prefix: str, subnets: list[LocalSubnet],
) -> tuple[str, str] | None:
    """Decode one tag into (zone, dest_cidr). None when the tag doesn't
    match the expected shape or references an unknown zone."""
    zones_by_upper = {s.zone.upper(): s.zone for s in subnets}
    if not tag.startswith(prefix):
        return None
    rest = tag[len(prefix):]
    for upper_zone in sorted(zones_by_upper.keys(), key=len, reverse=True):
        if rest.startswith(upper_zone + "_"):
            dest_slug = rest[len(upper_zone) + 1:]
            if "_" not in dest_slug:
                return None
            head, prefix_len = dest_slug.rsplit("_", 1)
            cidr = head.replace("_", ".") + "/" + prefix_len
            return (zones_by_upper[upper_zone], cidr)
    return None


# ---------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------

async def apply_state(
    ssh: SlateSSH,
    *,
    desired_rules: list[DesiredRule],
) -> dict:
    """Reconcile firewall toward `desired_rules`.

    Steps :
      1. Flush every iptables rule (FORWARD + nat POSTROUTING) tagged
         with one of our prefixes.
      2. Inject the wanted rules (FORWARD ACCEPT for every entry, +
         SNAT for entries with mode='snat').
      3. Rewrite the SC_FR_TS_SNAT block in `/etc/firewall.user` so the
         rules survive a `firewall reload`.

    `desired_rules` may legitimately be empty — that's the "no LAN can
    reach the tailnet" baseline.
    """
    state = await discover_state(ssh)
    needs_ts = any(r.via == "tailnet" for r in desired_rules)
    needs_wan = any(r.via == "wan" for r in desired_rules)
    needs_proton = any(r.via == "proton" for r in desired_rules)
    needs_tor = any(r.via == "tor" for r in desired_rules)
    if needs_ts and not state.tailscale_zone_exists:
        raise RuntimeError(
            f"firewall zone '{TS_ZONE_NAME}' missing — re-run the slate-ctrl "
            f"tailscale handler to ensure the zone is created."
        )
    if needs_ts and any(
        r.mode == "snat" and r.via == "tailnet" for r in desired_rules
    ) and not state.tailscale_self_ip:
        raise RuntimeError(
            "cannot apply tailnet SNAT — tailscale0 has no IPv4 address. "
            "Bring the Tailscale daemon up first."
        )
    if needs_wan and not state.wan_iface:
        raise RuntimeError(
            "cannot apply via=wan — couldn't resolve the WAN interface from "
            "the default route or UCI. Check the Slate's WAN configuration."
        )
    if needs_proton and not state.proton_iface:
        raise RuntimeError(
            "cannot apply via=proton — no Proton VPN tunnel interface "
            "found on the Slate. Configure Proton (Wireguard) first."
        )
    if needs_tor and not state.tor_active:
        raise RuntimeError(
            "cannot apply via=tor — Tor daemon isn't running on the Slate "
            f"or TransPort {TOR_TRANS_PORT} isn't listening. Start Tor "
            "first (Réseau → Tor → Démarrer)."
        )

    operations: list[str] = []

    # ---- 1. Flush our tagged rules. ---------------------------------
    operations.append(
        "iptables -S 2>/dev/null "
        f"| grep -E -- '--comment {FWD_TAG_PREFIX}' "
        "| while read line; do "
        "  chain=$(echo \"$line\" | awk '{print $2}'); "
        "  rest=$(echo \"$line\" | sed 's/^-A [^ ]* //'); "
        "  iptables -D $chain $rest 2>/dev/null; "
        "done; true"
    )
    operations.append(
        "iptables -t nat -S POSTROUTING 2>/dev/null "
        f"| grep -E -- '--comment {SNAT_TAG_PREFIX}' "
        "| while read line; do "
        "  rule=$(echo \"$line\" | sed 's/^-A POSTROUTING //'); "
        "  iptables -t nat -D POSTROUTING $rule 2>/dev/null; "
        "done; true"
    )
    # Flush previous Tor REDIRECT entries in PREROUTING.
    operations.append(
        "iptables -t nat -S PREROUTING 2>/dev/null "
        f"| grep -E -- '--comment {TOR_TAG_PREFIX}' "
        "| while read line; do "
        "  rule=$(echo \"$line\" | sed 's/^-A PREROUTING //'); "
        "  iptables -t nat -D PREROUTING $rule 2>/dev/null; "
        "done; true"
    )

    # ---- 2. Inject the wanted rules. --------------------------------
    by_zone: dict[str, str] = {s.zone: s.cidr for s in state.subnets}
    # Source iface per zone — needed by Tor PREROUTING REDIRECT, which
    # matches packets by incoming iface (br-<zone> in practice).
    iface_by_zone: dict[str, str] = {s.zone: s.iface for s in state.subnets}
    ts_ip = state.tailscale_self_ip or ""
    wan_iface = state.wan_iface or ""
    proton_iface = state.proton_iface or ""
    for rule in desired_rules:
        if rule.src_zone not in by_zone:
            continue  # zone disappeared between snapshot and apply

        # Tor takes a fundamentally different path : DNAT REDIRECT in
        # PREROUTING towards the Tor daemon on lo. No FORWARD/SNAT pair
        # because the Tor daemon eats the packet inside the same box.
        if rule.via == "tor":
            src_iface = iface_by_zone.get(rule.src_zone, "")
            if not src_iface:
                continue
            for proto, port in (("tcp", TOR_TRANS_PORT),
                                ("udp", TOR_DNS_PORT)):
                tor_tag = _tor_tag(rule.src_zone, rule.dest_cidr, proto)
                # For TCP we match any port ; for UDP we only redirect
                # destination port 53 (DNS) to TOR_DNS_PORT — avoids
                # blackholing every UDP flow.
                if proto == "tcp":
                    match = "-p tcp"
                else:
                    match = "-p udp --dport 53"
                operations.append(
                    f"iptables -t nat -I PREROUTING "
                    f"-i {shlex.quote(src_iface)} "
                    f"-d {shlex.quote(rule.dest_cidr)} "
                    f"{match} -j REDIRECT --to-port {port} "
                    f"-m comment --comment {shlex.quote(tor_tag)}"
                )
            continue  # Tor done — no forward/snat below

        # Resolve the egress interface from `via`. Already validated.
        out_iface = {
            "tailnet": TS_ZONE_NAME,
            "wan": wan_iface,
            "proton": proton_iface,
        }[rule.via]
        fwd_tag = _fwd_tag(rule.src_zone, rule.dest_cidr)
        chain = f"forwarding_{rule.src_zone}_rule"
        operations.append(
            f"iptables -I {chain} "
            f"-d {shlex.quote(rule.dest_cidr)} -o {shlex.quote(out_iface)} "
            f"-j ACCEPT -m comment --comment {shlex.quote(fwd_tag)}"
        )
        if rule.mode == "snat":
            snat_tag = _snat_tag(rule.src_zone, rule.dest_cidr)
            if rule.via == "tailnet":
                # Static SNAT to the Slate's tailnet IPv4 — the
                # destination peer responds to that IP, conntrack DNATs
                # the reply back to the LAN client.
                operations.append(
                    f"iptables -t nat -I POSTROUTING "
                    f"-s {shlex.quote(rule.src_cidr)} "
                    f"-d {shlex.quote(rule.dest_cidr)} "
                    f"-o {TS_ZONE_NAME} "
                    f"-j SNAT --to-source {shlex.quote(ts_ip)} "
                    f"-m comment --comment {shlex.quote(snat_tag)}"
                )
            elif rule.via in ("wan", "proton"):
                # MASQUERADE on the egress iface — the source IP is
                # picked by conntrack so we don't bake a dynamic address
                # into the rule (WAN DHCP renew, Proton WG reconnect).
                operations.append(
                    f"iptables -t nat -I POSTROUTING "
                    f"-s {shlex.quote(rule.src_cidr)} "
                    f"-d {shlex.quote(rule.dest_cidr)} "
                    f"-o {shlex.quote(out_iface)} "
                    f"-j MASQUERADE "
                    f"-m comment --comment {shlex.quote(snat_tag)}"
                )

    # ---- 3. Persist the SNAT block + the FORWARD ACCEPT block in
    #         /etc/firewall.user so reload re-applies. The forwarding
    #         chain rules also need persistence (fw3 reloads flush them).
    persist_lines = _build_persist_lines(
        desired_rules, by_zone, iface_by_zone, ts_ip, wan_iface, proton_iface,
    )
    operations.append(_rewrite_firewall_user_block(persist_lines))

    # The command list can grow large when an operator imports a preset
    # (Netflix → 9 CIDRs × 4 lines each = ~36 lines plus the persistence
    # block). One concatenated shell `;` chain crosses the SSH buffer
    # limit and the link drops. Batch into chunks small enough to stay
    # safely under the limit. ~20 operations per batch is a safe target.
    BATCH_SIZE = 20
    last_output = ""
    try:
        for i in range(0, len(operations), BATCH_SIZE):
            chunk = operations[i : i + BATCH_SIZE]
            r = await ssh.run(" ; ".join(chunk), timeout=60)
            last_output = r.stdout
    except SlateSSHError as exc:
        raise RuntimeError(f"apply failed: {exc}") from exc

    # Snapshot the post-state so the caller can show a diff to the user.
    new_state = await discover_state(ssh)
    logger.info(
        "tailscale.forwarding.applied",
        rules=len(desired_rules),
        snat=sum(1 for r in desired_rules if r.mode == "snat"),
        routed=sum(1 for r in desired_rules if r.mode == "routed"),
    )
    return {
        "ok": True,
        "applied_rules": len(desired_rules),
        "active_fwd": sorted(new_state.active_fwd),
        "active_snat": sorted(new_state.active_snat),
        "reload_output": last_output.strip()[-300:],
    }


def _build_persist_lines(
    rules: list[DesiredRule],
    by_zone_cidr: dict[str, str],
    iface_by_zone: dict[str, str],
    ts_ip: str,
    wan_iface: str,
    proton_iface: str,
) -> list[str]:
    """Return the shell lines that recreate every active rule.

    Persisted in `/etc/firewall.user` so a `firewall reload` re-runs
    them. Each line is paired with a leading `-D` so the script is safe
    to run multiple times.
    """
    lines: list[str] = []
    for rule in rules:
        if rule.src_zone not in by_zone_cidr:
            continue

        # Tor : DNAT REDIRECT in PREROUTING. No FORWARD/SNAT pair.
        if rule.via == "tor":
            src_iface = iface_by_zone.get(rule.src_zone, "")
            if not src_iface:
                continue
            for proto, port in (("tcp", TOR_TRANS_PORT),
                                ("udp", TOR_DNS_PORT)):
                tor_tag = _tor_tag(rule.src_zone, rule.dest_cidr, proto)
                if proto == "tcp":
                    match = "-p tcp"
                else:
                    match = "-p udp --dport 53"
                tor_tail = (
                    f"-i {src_iface} -d {rule.dest_cidr} {match} "
                    f"-j REDIRECT --to-port {port} "
                    f"-m comment --comment {tor_tag}"
                )
                lines.append(
                    f"iptables -t nat -D PREROUTING {tor_tail} 2>/dev/null"
                )
                lines.append(
                    f"iptables -t nat -I PREROUTING {tor_tail}"
                )
            continue

        out_iface = {
            "tailnet": TS_ZONE_NAME,
            "wan": wan_iface,
            "proton": proton_iface,
        }.get(rule.via, "")
        if not out_iface:
            continue
        fwd_tag = _fwd_tag(rule.src_zone, rule.dest_cidr)
        chain = f"forwarding_{rule.src_zone}_rule"
        rule_tail = (
            f"-d {rule.dest_cidr} -o {out_iface} "
            f"-j ACCEPT -m comment --comment {fwd_tag}"
        )
        lines.append(f"iptables -D {chain} {rule_tail} 2>/dev/null")
        lines.append(f"iptables -I {chain} {rule_tail}")
        if rule.mode == "snat":
            snat_tag = _snat_tag(rule.src_zone, rule.dest_cidr)
            if rule.via == "tailnet" and ts_ip:
                snat_tail = (
                    f"-s {rule.src_cidr} -d {rule.dest_cidr} -o {TS_ZONE_NAME} "
                    f"-j SNAT --to-source {ts_ip} "
                    f"-m comment --comment {snat_tag}"
                )
            elif rule.via in ("wan", "proton"):
                snat_tail = (
                    f"-s {rule.src_cidr} -d {rule.dest_cidr} -o {out_iface} "
                    f"-j MASQUERADE "
                    f"-m comment --comment {snat_tag}"
                )
            else:
                continue
            lines.append(
                f"iptables -t nat -D POSTROUTING {snat_tail} 2>/dev/null"
            )
            lines.append(
                f"iptables -t nat -I POSTROUTING {snat_tail}"
            )
    return lines


def _rewrite_firewall_user_block(persist_lines: list[str]) -> str:
    body = "\n".join(persist_lines)
    new_block = f"{BLOCK_BEGIN}\n{body}\n{BLOCK_END}"
    return (
        "FILE=/etc/firewall.user; "
        "touch \"$FILE\"; "
        f"awk -v begin='{BLOCK_BEGIN}' -v end='{BLOCK_END}' "
        "'BEGIN{drop=0} "
        " $0==begin{drop=1; next} "
        " $0==end{drop=0; next} "
        " drop==0{print}' \"$FILE\" > \"$FILE.tmp\"; "
        f"printf '%s\\n' {shlex.quote(new_block)} >> \"$FILE.tmp\"; "
        "mv \"$FILE.tmp\" \"$FILE\"; "
        "chmod +x \"$FILE\""
    )


# ---------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------

def _parse_uci_dump(
    text: str,
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, dict[str, str]],
    str | None,
    str | None,
    str | None,
    bool,
    set[str],
    set[str],
    set[str],
]:
    networks: dict[str, dict[str, str]] = {}
    zones_raw: dict[str, dict[str, str]] = {}
    ts_ip: str | None = None
    wan_iface: str | None = None
    proton_iface: str | None = None
    tor_active = False
    snat_tags: set[str] = set()
    fwd_tags: set[str] = set()
    tor_tags: set[str] = set()
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("---") and line.endswith("---"):
            section = line.strip("- ").lower()
            continue
        if section == "ts_ip":
            if "/" in line:
                ts_ip = line.split("/", 1)[0].strip() or None
            continue
        if section == "wan_iface":
            # First non-empty line wins — the command emitted either the
            # default-route iface or the UCI device.
            if line and wan_iface is None:
                wan_iface = line
            continue
        if section == "proton_iface":
            if line and proton_iface is None:
                proton_iface = line
            continue
        if section == "tor_active":
            if line in ("yes", "no") and tor_active is False:
                tor_active = (line == "yes")
            continue
        if section in ("snat_tags", "fwd_tags", "tor_tags"):
            idx = line.find("--comment ")
            if idx >= 0:
                rest = line[idx + len("--comment ") :]
                tag = rest.strip().split()[0].strip("\"'")
                if section == "snat_tags" and tag.startswith(SNAT_TAG_PREFIX):
                    snat_tags.add(tag)
                if section == "fwd_tags" and tag.startswith(FWD_TAG_PREFIX):
                    fwd_tags.add(tag)
                if section == "tor_tags" and tag.startswith(TOR_TAG_PREFIX):
                    tor_tags.add(tag)
            continue
        if "=" not in line:
            continue
        left, _, right = line.partition("=")
        parts = left.split(".")
        if len(parts) < 3:
            continue
        sec_name = parts[1]
        key = parts[2]
        val = right.strip("'\"")
        if section == "networks":
            networks.setdefault(sec_name, {})[key] = val
        elif section in ("zones", "ts_zone"):
            zones_raw.setdefault(sec_name, {})[key] = val
    zones_by_name: dict[str, dict[str, str]] = {}
    for sec, kv in zones_raw.items():
        nm = kv.get("name") or sec
        kv2 = dict(kv)
        kv2["_section"] = sec
        zones_by_name[nm] = kv2
    return (
        networks, zones_by_name, ts_ip, wan_iface, proton_iface, tor_active,
        snat_tags, fwd_tags, tor_tags,
    )


def _zone_for_network(zones: dict, net_slug: str) -> str | None:
    for zone_name, kv in zones.items():
        net = kv.get("network", "")
        if net and net_slug in net.split():
            return zone_name
    return None


def _ip_netmask_to_cidr(ip: str, mask: str) -> str:
    try:
        ip_parts = [int(p) for p in ip.split(".")]
        mask_parts = [int(p) for p in mask.split(".")]
        if len(ip_parts) != 4 or len(mask_parts) != 4:
            return f"{ip}/24"
        prefix = sum(bin(m).count("1") for m in mask_parts)
        net_parts = [ip_parts[i] & mask_parts[i] for i in range(4)]
        return f"{net_parts[0]}.{net_parts[1]}.{net_parts[2]}.{net_parts[3]}/{prefix}"
    except (ValueError, TypeError):
        return f"{ip}/24"
