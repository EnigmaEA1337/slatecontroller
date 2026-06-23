"""Per-network egress reconciler for the Fortinet SSL VPN tunnel.

Walks every :class:`NetworkRow` and, for each one with
``egress_via_forti=True``, installs the iptables + policy-routing rules
that steer **all** of that bridge's traffic out through the openfortivpn
``ppp`` interface (typically ``ppp0``).

Persistence model — identical to ``app.tailscale.forwarding`` and
``app.tailscale.dns_routing`` : the operations are pushed live AND
appended (as a self-contained shell block) to ``/etc/firewall.user``
between balise markers so a ``fw3 reload`` re-applies them. This keeps
the rules surviving every profile activation (firewall.sh reloads fw3)
without needing UCI section entries — which would clash with the
network handler's orphan purger.

Rule shapes (every rule carries a `-m comment --comment` tag for
diagnostic + idempotent flush) :

  SC_FR_FORTI_SNAT_<NETWORK>
    ``iptables -t nat -I POSTROUTING -s <subnet> -o <ppp> -j MASQUERADE``
    Source-NAT for the bridge traffic going out through the ppp iface.

  SC_FR_FORTI_RULE_<NETWORK>
    ``ip rule add from <subnet> table <table_id> priority 5400``
    Sends every packet from this subnet to a per-network routing table.

  SC_FR_FORTI_ROUTE_<NETWORK>
    ``ip route add default dev <ppp> table <table_id>``
    Default-route through the tunnel inside that table.

  SC_FR_FORTI_KS_<NETWORK>   (only when ``forti_kill_switch=True`` AND
                              the tunnel is DOWN)
    ``iptables -I FORWARD -i br-<network> -o <wan_iface> -j REJECT``
    Fail-closed safety net : when the tunnel is gone, drop the bridge's
    bytes rather than leak them out the WAN in clear.

Routing-table IDs are derived deterministically from a hash of the
network slug (range 250..899) so re-deploys are stable. Free of clash
with the DNS routing reconciler (100..899) because we restrict our
window to 250..899 and DNS routing's range is 100..249-ish in practice.
"""

from __future__ import annotations

import hashlib
import shlex

import structlog

from app.exceptions import SlateError
from app.networks.store import NetworkStore
from app.slate.ssh import SlateSSH, SlateSSHError
from app.vpn.fortinet.manager import FortinetManager


logger = structlog.get_logger(__name__)


SNAT_TAG_PREFIX = "SC_FR_FORTI_SNAT_"
KS_TAG_PREFIX = "SC_FR_FORTI_KS_"
RULE_TAG_PREFIX = "SC_FR_FORTI_RULE_"
ROUTE_TAG_PREFIX = "SC_FR_FORTI_ROUTE_"

BLOCK_BEGIN = (
    "### SC_FR_FORTI block - managed by slate-controller - do not edit ###"
)
BLOCK_END = "### SC_FR_FORTI block end ###"

# `ip rule` priority. 5400 keeps Forti BELOW the DNS-routing tables
# (5000) so a fwmark match still wins when both could apply — DNS-based
# routing is finer-grained intent than full-bridge egress and should
# preempt. Above 32766 (default main) so our rule actually fires.
RULE_PRIORITY = 5400


class FortiNetworkRoutingError(SlateError):
    pass


def _network_slug_tag(slug: str) -> str:
    """Sanitise a slug for use inside iptables --comment / shell tags."""
    return "".join(ch if ch.isalnum() else "_" for ch in slug).upper()


def _route_table_for(slug: str) -> int:
    """Deterministic [250, 899] table id from a sha1 of the slug."""
    h = int.from_bytes(hashlib.sha1(slug.encode()).digest()[:4], "big")
    return 250 + (h % (900 - 250))


def _build_flush_lines() -> list[str]:
    """Shell snippets that remove every SC_FR_FORTI_* iptables tag, every
    ``ip rule`` matching our priority, and every per-table route the
    previous apply may have installed.

    Idempotent : if nothing matches, ``true`` is returned successfully.
    """
    return [
        # 1) nat POSTROUTING MASQUERADE entries
        (
            "iptables -t nat -S POSTROUTING 2>/dev/null "
            f"| grep -E -- '--comment {SNAT_TAG_PREFIX}' "
            "| sed 's/^-A /-D /' "
            "| while read -r rule ; do iptables -t nat -D POSTROUTING $rule 2>/dev/null ; done"
        ),
        # 2) filter FORWARD kill-switch REJECT entries
        (
            "iptables -S 2>/dev/null "
            f"| grep -E -- '--comment {KS_TAG_PREFIX}' "
            "| sed 's/^-A /-D /' "
            "| while read -r rule ; do "
            "    chain=$(echo $rule | awk '{print $2}'); "
            "    rest=$(echo $rule | cut -d' ' -f3-); "
            "    iptables -D $chain $rest 2>/dev/null ; "
            "  done"
        ),
        # 3) ip rule entries at our priority
        (
            f"ip rule show pref {RULE_PRIORITY} 2>/dev/null "
            "| sed 's/^[0-9]*:[[:space:]]*//' "
            "| while read -r r ; do ip rule del pref "
            f"{RULE_PRIORITY} $r 2>/dev/null ; done"
        ),
    ]


def _build_apply_lines(
    *,
    subnets: list[tuple[str, str, str, bool]],
    ppp_iface: str,
    wan_iface: str,
    tunnel_up: bool,
) -> list[str]:
    """Build the apply portion of the firewall.user block.

    ``subnets`` items : ``(slug, subnet_cidr, bridge_name, kill_switch)``.
    Only rows with ``egress_via_forti=True`` are passed in.

    When ``tunnel_up=False`` we still install the kill-switch REJECTs for
    nets that opted in — that's the whole point of fail-closed. The SNAT
    + ip rule + ip route are skipped because there's no ppp iface to
    target.
    """
    lines: list[str] = []
    for slug, cidr, bridge, ks_on in subnets:
        slug_tag = _network_slug_tag(slug)
        table = _route_table_for(slug)
        snat_tag = f"{SNAT_TAG_PREFIX}{slug_tag}"
        rule_tag = f"{RULE_TAG_PREFIX}{slug_tag}"
        route_tag = f"{ROUTE_TAG_PREFIX}{slug_tag}"
        ks_tag = f"{KS_TAG_PREFIX}{slug_tag}"

        if tunnel_up and ppp_iface:
            lines.append(
                f"iptables -t nat -I POSTROUTING "
                f"-s {shlex.quote(cidr)} -o {shlex.quote(ppp_iface)} "
                f"-j MASQUERADE -m comment --comment {shlex.quote(snat_tag)}"
            )
            lines.append(
                f"ip rule add from {shlex.quote(cidr)} table {table} "
                f"priority {RULE_PRIORITY} "
                f"2>/dev/null || true   # {rule_tag}"
            )
            lines.append(
                f"ip route replace default dev {shlex.quote(ppp_iface)} "
                f"table {table}   # {route_tag}"
            )

        if ks_on and bridge and wan_iface:
            # The kill-switch REJECT fires when the packet would otherwise
            # leak to WAN. We put it on FORWARD with -i br-<network> -o
            # <wan_iface> so it covers BOTH cases :
            #   - tunnel DOWN : there's no ip rule -> packet falls through
            #     to main table -> default route via WAN -> REJECT fires.
            #   - tunnel UP : ip rule -> custom table -> default via ppp,
            #     the packet never reaches FORWARD with -o wan, so the
            #     rule is inert (no false-positive drops).
            lines.append(
                f"iptables -I FORWARD "
                f"-i {shlex.quote(bridge)} -o {shlex.quote(wan_iface)} "
                f"-j REJECT --reject-with icmp-net-prohibited "
                f"-m comment --comment {shlex.quote(ks_tag)}"
            )
    return lines


def _rewrite_firewall_user_block(persist_lines: list[str]) -> str:
    """Drop-in awk that replaces (or inserts) the SC_FR_FORTI balise block
    in ``/etc/firewall.user``. Same idiom as the other reconcilers."""
    body = "\n".join(persist_lines).replace("'", "'\\''")
    return (
        "FILE=/etc/firewall.user ; "
        "TMP=$(mktemp) ; "
        "touch $FILE ; "
        f"awk -v begin='{BLOCK_BEGIN}' -v end='{BLOCK_END}' "
        "  ' "
        " $0==begin{drop=1; next} "
        " $0==end{drop=0; next} "
        " !drop{print} "
        "  ' $FILE > $TMP ; "
        f"{{ echo '{BLOCK_BEGIN}' ; printf '%s\\n' '{body}' ; "
        f"echo '{BLOCK_END}' ; }} >> $TMP ; "
        "mv $TMP $FILE ; chmod 600 $FILE"
    )


async def _discover_ifaces(ssh: SlateSSH) -> tuple[str, str]:
    """Return ``(wan_iface, ppp_iface)`` snapshot of the live state.

    Both can be empty strings — the caller copes with that gracefully
    (e.g. no kill-switch installed when WAN iface is unknown).
    """
    cmd = (
        "export PATH=/usr/sbin:/sbin:/usr/bin:/bin:$PATH ; "
        "wan=$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}') ; "
        "ppp=$(ls /sys/class/net 2>/dev/null | grep '^ppp' | head -1) ; "
        "echo \"wan=$wan\" ; "
        "echo \"ppp=$ppp\""
    )
    try:
        r = await ssh.run(cmd, timeout=10)
    except SlateSSHError as exc:
        raise FortiNetworkRoutingError(f"discover ifaces: {exc}") from exc
    wan, ppp = "", ""
    for line in (r.stdout or "").splitlines():
        if line.startswith("wan="):
            wan = line.split("=", 1)[1].strip()
        elif line.startswith("ppp="):
            ppp = line.split("=", 1)[1].strip()
    return wan, ppp


async def reconcile(
    ssh: SlateSSH,
    network_store: NetworkStore,
    forti_manager: FortinetManager,
) -> dict:
    """Full reconciliation pass — call after every relevant state change :

      - Forti tunnel up/down
      - Network catalog edit (egress_via_forti or forti_kill_switch flag)
      - Profile activation (rules might have been flushed by fw3 reload —
        ours are persisted in firewall.user so this is mostly cosmetic,
        but it surfaces drift if it happens).

    Returns a small report dict the caller can echo to logs / UI.
    """
    # Snapshot inputs.
    wan_iface, ppp_iface = await _discover_ifaces(ssh)
    status = await forti_manager.status()
    tunnel_up = status.state == "up"
    if tunnel_up and not ppp_iface:
        # Manager says UP but no ppp netdev — disagree. Treat as down :
        # we won't install SNAT/route entries that would target nothing.
        tunnel_up = False

    nets = await network_store.list_all()
    subnets: list[tuple[str, str, str, bool]] = []
    for n in nets:
        if not n.egress_via_forti:
            continue
        subnets.append(
            (n.slug, n.subnet_cidr, n.bridge_name, bool(n.forti_kill_switch))
        )

    flush_lines = _build_flush_lines()
    # Flush leftover per-table routes for the slugs we know — cheap and
    # guaranteed-safe : flushing an empty table is a no-op.
    for slug, _, _, _ in subnets:
        table = _route_table_for(slug)
        flush_lines.append(f"ip route flush table {table} 2>/dev/null || true")

    apply_lines = _build_apply_lines(
        subnets=subnets,
        ppp_iface=ppp_iface,
        wan_iface=wan_iface,
        tunnel_up=tunnel_up,
    )

    persist_lines = flush_lines + apply_lines
    persist_lines.append("exit 0")

    # 1) Apply live (idempotent).
    script = "; ".join(persist_lines)
    try:
        await ssh.run(f"PATH=/usr/sbin:/sbin:/usr/bin:/bin sh -c {shlex.quote(script)}", timeout=30)
    except SlateSSHError as exc:
        raise FortiNetworkRoutingError(f"apply live: {exc}") from exc

    # 2) Persist into /etc/firewall.user so fw3 reload re-runs them.
    rewrite = _rewrite_firewall_user_block(persist_lines)
    try:
        await ssh.run(f"PATH=/usr/sbin:/sbin:/usr/bin:/bin sh -c {shlex.quote(rewrite)}", timeout=15)
    except SlateSSHError as exc:
        raise FortiNetworkRoutingError(f"rewrite firewall.user: {exc}") from exc

    report = {
        "tunnel_up": tunnel_up,
        "ppp_iface": ppp_iface or None,
        "wan_iface": wan_iface or None,
        "networks": [s[0] for s in subnets],
        "applied_lines": len(apply_lines),
    }
    logger.info("forti.network_routing.reconcile", **report)
    return report
