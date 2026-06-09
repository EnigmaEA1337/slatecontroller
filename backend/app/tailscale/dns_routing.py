"""DNS-based reverse routing — route every IP resolved for a domain
through a chosen egress (Tailscale / WAN / Proton / Tor).

Stack on the Slate :
  - dnsmasq (port 53, primary resolver for LAN clients) is given an
    `ipset=/<domain>/<ipset_name>` directive per rule, pushed through a
    drop-in file at /etc/dnsmasq.d/slate-ctrl-policies.conf
  - one `ipset` (hash:ip family inet timeout 3600) per rule, named
    `slate_<zone>_<label>`
  - one `iptables -t mangle -A PREROUTING -i <bridge> -m set
    --match-set <ipset> dst -j MARK --set-mark 0x<mark>` per rule
  - one `ip rule add fwmark 0x<mark> table <table>` per rule
  - one `ip route add default <egress> table <table>` per rule
  - persistence : the iptables + ip rules go into `/etc/firewall.user`'s
    SC_FR_DNS block ; the dnsmasq conf file is written directly and
    survives reboot ; the ipsets are recreated by the persistence
    script (they don't survive reboot otherwise)

Naming convention (kept short to fit the 31-char ipset limit) :
  - ipset name        : `slate_<zone>_<label>`  (up to 30 chars)
  - mangle tag        : `SC_FR_DNS_<UPPERZONE>_<LABEL>`
  - persistent block  : `### SC_FR_DNS block - managed by slate-controller ###`
  - dnsmasq drop-in   : `/etc/dnsmasq.d/slate-ctrl-policies.conf`

fwmark + routing-table allocation : the mark is a deterministic
function of (zone, label) so two reconciles in a row produce the same
marks (idempotent). We hash the pair into a 16-bit mark in the range
0x0100..0xFEFF and pick the routing-table id at `100 + mark % 800`. This
gives us 800 distinct tables and is unlikely to collide with anything
the Tailscale or fw3 stacks reserve (Tailscale uses 0x40000/0x80000
upper bits and table 52).
"""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass, field
from typing import Literal

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


# Constants kept in sync with `forwarding.py` so the iptables flush
# patterns in `apply_state` don't trip on each other.
DNS_TAG_PREFIX = "SC_FR_DNS_"
DNSMASQ_CONFDIR = "/tmp/dnsmasq.d"
DNSMASQ_CONF_PATH = f"{DNSMASQ_CONFDIR}/slate-ctrl-policies.conf"
PERSIST_BLOCK_BEGIN = (
    "### SC_FR_DNS block - managed by slate-controller - do not edit ###"
)
PERSIST_BLOCK_END = "### SC_FR_DNS block end ###"

NatMode = Literal["routed", "snat"]
Via = Literal["tailnet", "wan", "proton", "tor"]


@dataclass(frozen=True)
class DesiredDomainRule:
    """The reconciler input. Each instance becomes one ipset + one
    mangle MARK rule + one ip rule + one ip route entry."""

    zone: str          # firewall/network zone (e.g. "nexus")
    src_iface: str     # bridge iface that carries the LAN (e.g. "br-nexus")
    label: str         # short id used in the ipset name
    domains: list[str] # DNS names dnsmasq watches
    mode: NatMode
    via: Via
    # Egress runtime resolved by the caller from the snapshot :
    #   - "tailnet" → "tailscale0", "100.x.y.z"
    #   - "wan"     → "<wan_iface>", ""
    #   - "proton"  → "<proton_iface>", ""
    #   - "tor"     → not used for DNS routing here (Tor for DNS is
    #                 already handled via tor_route_mode on the network)
    egress_iface: str
    egress_snat_ip: str  # only set for via="tailnet"


@dataclass(frozen=True)
class DnsRoutingSnapshot:
    """Live state read from the Slate."""

    ipset_installed: bool = False
    dnsmasq_path: str = ""
    # set of `slate_*` ipsets currently present on the Slate
    active_ipsets: set[str] = field(default_factory=set)
    # set of (zone, label) pairs whose mangle MARK rule is active
    active_marks: set[tuple[str, str]] = field(default_factory=set)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _ipset_name(zone: str, label: str) -> str:
    """Return the ipset name for one rule.

    Capped at 31 chars by netfilter. We assume operator labels stay
    short (validated upstream) so the name fits ; we still truncate as a
    last resort to avoid runtime errors.
    """
    raw = f"slate_{zone}_{label}".lower()
    return raw[:31]


def _mangle_tag(zone: str, label: str) -> str:
    return f"{DNS_TAG_PREFIX}{zone.upper()}_{label.upper()}"


def _fwmark_for(zone: str, label: str) -> int:
    """Return a deterministic 16-bit fwmark for the (zone, label) pair.

    Range : 0x0100..0xFEFF. The low byte is the table id offset so two
    different rules tend to hash to different tables too.
    """
    h = hashlib.sha1(f"{zone}|{label}".encode()).digest()
    raw = int.from_bytes(h[:2], "big")
    # Force into 0x0100..0xFEFF to dodge Tailscale's marks (which use
    # 0x40000+ — way above our 16-bit range, but keep the floor anyway).
    return 0x0100 + (raw % 0xFE00)


def _route_table_for(zone: str, label: str) -> int:
    """Routing table id for one rule. Range : 100..899."""
    return 100 + (_fwmark_for(zone, label) % 800)


# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------

async def discover_state(ssh: SlateSSH) -> DnsRoutingSnapshot:
    """Snapshot of what's currently configured on the Slate."""
    cmd = (
        "echo '---IPSET---'; "
        "which ipset 2>/dev/null && echo yes || echo no; "
        "echo '---DNSMASQ---'; "
        "which dnsmasq; "
        "echo '---ACTIVE_IPSETS---'; "
        "ipset list -n 2>/dev/null | grep '^slate_' || true; "
        "echo '---ACTIVE_MARKS---'; "
        "iptables -t mangle -S PREROUTING 2>/dev/null "
        f"| grep -E -- '--comment {DNS_TAG_PREFIX}' || true"
    )
    try:
        r = await ssh.run(cmd, timeout=15)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH discover failed: {exc}") from exc

    ipset_installed = False
    dnsmasq_path = ""
    active_ipsets: set[str] = set()
    active_marks: set[tuple[str, str]] = set()
    section = ""
    for raw in r.stdout.splitlines():
        line = raw.strip()
        if line.startswith("---") and line.endswith("---"):
            section = line.strip("- ").lower()
            continue
        if not line:
            continue
        if section == "ipset" and line == "yes":
            ipset_installed = True
        elif section == "ipset" and line.startswith("/"):
            ipset_installed = True
        elif section == "dnsmasq" and line.startswith("/"):
            dnsmasq_path = line
        elif section == "active_ipsets" and line.startswith("slate_"):
            active_ipsets.add(line)
        elif section == "active_marks":
            idx = line.find("--comment ")
            if idx >= 0:
                rest = line[idx + len("--comment ") :]
                tag = rest.strip().split()[0].strip("\"'")
                if tag.startswith(DNS_TAG_PREFIX):
                    rest_after_prefix = tag[len(DNS_TAG_PREFIX):]
                    if "_" in rest_after_prefix:
                        upper_zone, upper_label = rest_after_prefix.split("_", 1)
                        active_marks.add(
                            (upper_zone.lower(), upper_label.lower()),
                        )

    return DnsRoutingSnapshot(
        ipset_installed=ipset_installed,
        dnsmasq_path=dnsmasq_path,
        active_ipsets=active_ipsets,
        active_marks=active_marks,
    )


# ---------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------

async def apply_state(
    ssh: SlateSSH,
    *,
    desired: list[DesiredDomainRule],
) -> dict:
    """Reconcile DNS-based routing toward `desired`.

    Idempotent ; safe to re-run.
    """
    state = await discover_state(ssh)
    if desired and not state.ipset_installed:
        raise RuntimeError(
            "ipset binary not found on the Slate. Install it via "
            "`opkg install ipset` (will be added to the adoption "
            "pipeline)."
        )
    if desired and not state.dnsmasq_path:
        raise RuntimeError(
            "dnsmasq not found on the Slate — domain-based DNS routing "
            "requires dnsmasq as the primary resolver."
        )

    # ---- 1. Build the dnsmasq drop-in conf content. ------------------
    dnsmasq_lines: list[str] = [
        "# This file is generated by slate-controller. Do not edit manually.",
        "# Each `ipset=/<domain>/<ipset>` line tells dnsmasq to push every "
        "resolved IP",
        "# for <domain> into the named ipset. The mangle MARK rules and "
        "policy",
        "# routing entries are in /etc/firewall.user.",
        "",
    ]
    for rule in desired:
        ipset_name = _ipset_name(rule.zone, rule.label)
        for domain in rule.domains:
            # dnsmasq accepts plain domain or `.domain` for subdomain
            # match ; we pass through whatever the operator entered.
            dnsmasq_lines.append(f"ipset=/{domain}/{ipset_name}")

    dnsmasq_conf = "\n".join(dnsmasq_lines) + "\n"

    # ---- 2. Build the persistence script (firewall.user block). ------
    # The script :
    #   a. (re)creates every wanted ipset
    #   b. destroys orphaned `slate_*` ipsets
    #   c. inserts the mangle MARK rule per (zone, label) pair
    #   d. inserts the ip rule + ip route per pair
    # Each step starts with a `delete-if-exists` so the script is
    # idempotent and safe to re-run.
    persist_lines: list[str] = []
    wanted_ipsets: set[str] = set()
    for rule in desired:
        ipset_name = _ipset_name(rule.zone, rule.label)
        wanted_ipsets.add(ipset_name)
        tag = _mangle_tag(rule.zone, rule.label)
        mark = _fwmark_for(rule.zone, rule.label)
        table = _route_table_for(rule.zone, rule.label)

        # (a) ipset create. `-exist` so an existing one is left alone.
        persist_lines.append(
            f"ipset create {ipset_name} hash:ip family inet timeout 3600 -exist"
        )

        # (c) mangle PREROUTING : MARK packets whose dst is in the set.
        mangle_tail = (
            f"-i {rule.src_iface} -m set --match-set {ipset_name} dst "
            f"-j MARK --set-mark 0x{mark:x} "
            f"-m comment --comment {tag}"
        )
        persist_lines.append(
            f"iptables -t mangle -D PREROUTING {mangle_tail} 2>/dev/null"
        )
        persist_lines.append(f"iptables -t mangle -I PREROUTING {mangle_tail}")

        # (d) ip rule + ip route.
        persist_lines.append(
            f"ip rule del fwmark 0x{mark:x} table {table} 2>/dev/null"
        )
        persist_lines.append(
            f"ip rule add fwmark 0x{mark:x} table {table} priority 5000"
        )
        persist_lines.append(f"ip route flush table {table} 2>/dev/null")
        if rule.via == "tailnet":
            persist_lines.append(
                f"ip route add default dev {rule.egress_iface} table {table}"
            )
        else:
            persist_lines.append(
                f"ip route add default dev {rule.egress_iface} table {table}"
            )
        # SNAT/MASQUERADE — reuse the same shape as forwarding.py for
        # `via=tailnet` (static SNAT) and other vias (MASQUERADE).
        if rule.mode == "snat":
            mark_match = f"-m mark --mark 0x{mark:x}"
            if rule.via == "tailnet" and rule.egress_snat_ip:
                persist_lines.append(
                    f"iptables -t nat -D POSTROUTING {mark_match} "
                    f"-o {rule.egress_iface} -j SNAT --to-source "
                    f"{rule.egress_snat_ip} -m comment --comment {tag} "
                    f"2>/dev/null"
                )
                persist_lines.append(
                    f"iptables -t nat -I POSTROUTING {mark_match} "
                    f"-o {rule.egress_iface} -j SNAT --to-source "
                    f"{rule.egress_snat_ip} -m comment --comment {tag}"
                )
            else:
                persist_lines.append(
                    f"iptables -t nat -D POSTROUTING {mark_match} "
                    f"-o {rule.egress_iface} -j MASQUERADE "
                    f"-m comment --comment {tag} 2>/dev/null"
                )
                persist_lines.append(
                    f"iptables -t nat -I POSTROUTING {mark_match} "
                    f"-o {rule.egress_iface} -j MASQUERADE "
                    f"-m comment --comment {tag}"
                )

    # (b) destroy orphans
    for stale in sorted(state.active_ipsets - wanted_ipsets):
        persist_lines.append(f"ipset destroy {stale} 2>/dev/null")

    # ---- 3. Execute via a batched SSH pipeline. ---------------------
    operations: list[str] = []
    # Push the dnsmasq conf file via openssl base64 (busybox safe).
    # We `mkdir -p` first because the OpenWrt GL.iNet build uses
    # `/tmp/dnsmasq.d` (tmpfs) as the confdir, and that path may not yet
    # exist after a boot before dnsmasq populates it.
    import base64 as _b64
    b64 = _b64.b64encode(dnsmasq_conf.encode()).decode()
    operations.append(
        f"mkdir -p {DNSMASQ_CONFDIR} && "
        f"echo {b64} | openssl base64 -d > {DNSMASQ_CONF_PATH}.tmp && "
        f"mv {DNSMASQ_CONF_PATH}.tmp {DNSMASQ_CONF_PATH}"
    )
    # When desired is empty we still want the conf cleared.
    if not desired:
        operations.append(f"echo '' > {DNSMASQ_CONF_PATH}")

    # Flush all our mangle/SNAT entries before re-applying — covers the
    # case where a rule was removed entirely between snapshots.
    operations.append(
        "iptables -t mangle -S PREROUTING 2>/dev/null "
        f"| grep -E -- '--comment {DNS_TAG_PREFIX}' "
        "| while read line; do "
        "  rule=$(echo \"$line\" | sed 's/^-A PREROUTING //'); "
        "  iptables -t mangle -D PREROUTING $rule 2>/dev/null; "
        "done; true"
    )
    operations.append(
        "iptables -t nat -S POSTROUTING 2>/dev/null "
        f"| grep -E -- '--comment {DNS_TAG_PREFIX}' "
        "| while read line; do "
        "  rule=$(echo \"$line\" | sed 's/^-A POSTROUTING //'); "
        "  iptables -t nat -D POSTROUTING $rule 2>/dev/null; "
        "done; true"
    )
    # Replay the persist script lines.
    operations.extend(persist_lines)

    # Rewrite the SC_FR_DNS block in /etc/firewall.user (idempotent).
    operations.append(_rewrite_firewall_user_block(persist_lines))

    # Restart dnsmasq so it picks up the new ipset= directives. SIGHUP
    # alone (or `reload`) doesn't re-read drop-ins — only a real restart
    # does. The restart momentarily disrupts DHCP service but dnsmasq is
    # usually back in <1 s ; we don't `wait`.
    operations.append("/etc/init.d/dnsmasq restart 2>&1 | tail -3 || true")

    BATCH_SIZE = 20
    last_output = ""
    try:
        for i in range(0, len(operations), BATCH_SIZE):
            chunk = operations[i : i + BATCH_SIZE]
            r = await ssh.run(" ; ".join(chunk), timeout=60)
            last_output = r.stdout
    except SlateSSHError as exc:
        raise RuntimeError(f"DNS routing apply failed: {exc}") from exc

    logger.info(
        "dns_routing.applied",
        rules=len(desired),
        ipsets=len(wanted_ipsets),
        orphans_destroyed=len(state.active_ipsets - wanted_ipsets),
    )
    return {
        "ok": True,
        "applied_rules": len(desired),
        "ipsets": sorted(wanted_ipsets),
        "destroyed_orphans": sorted(state.active_ipsets - wanted_ipsets),
        "reload_output": last_output.strip()[-300:],
    }


def _rewrite_firewall_user_block(persist_lines: list[str]) -> str:
    body = "\n".join(persist_lines)
    new_block = f"{PERSIST_BLOCK_BEGIN}\n{body}\n{PERSIST_BLOCK_END}"
    return (
        "FILE=/etc/firewall.user; "
        "touch \"$FILE\"; "
        f"awk -v begin='{PERSIST_BLOCK_BEGIN}' -v end='{PERSIST_BLOCK_END}' "
        "'BEGIN{drop=0} "
        " $0==begin{drop=1; next} "
        " $0==end{drop=0; next} "
        " drop==0{print}' \"$FILE\" > \"$FILE.tmp\"; "
        f"printf '%s\\n' {shlex.quote(new_block)} >> \"$FILE.tmp\"; "
        "mv \"$FILE.tmp\" \"$FILE\"; "
        "chmod +x \"$FILE\""
    )
