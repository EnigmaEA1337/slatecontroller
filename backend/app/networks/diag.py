"""Live L2/L3 diagnostic collector — reads from the running Slate via SSH.

Sources used:
  - `ip -j addr show`               → IPv4/IPv6 per interface
  - `ip -j link show`               → interfaces + master bridge + operstate
  - `ip -j route show table all`    → IPv4 routes ACROSS ALL TABLES
                                      (main + local + Tailscale 52 + GL.iNet
                                      multiwan tables + per-VPN tables)
  - `ip -j -6 route show table all` → IPv6 routes across all tables
  - `ip -j rule`                    → policy routing (which table for which
                                      packets — needed to read multi-table
                                      setups like Tailscale's table 52)
  - `ip -j neigh`                   → ARP / NDP table (L2 neighbours)
  - `/proc/net/dev`                 → traffic counters per interface (delta
                                      over time is computed client-side from
                                      successive snapshots)
  - `ubus call network.interface dump` → UCI-level interface metadata
                                      (proto, dns-server, ipv6-prefix, …)

Read-only: no command modifies state.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


def _safe_json_loads(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.info("netdiag.json_parse_failed", error=str(exc), sample=text[:120])
        return None


def _parse_proc_net_dev(text: str) -> dict[str, dict[str, int]]:
    """Parse /proc/net/dev → {iface: {rx_bytes, rx_packets, tx_bytes, tx_packets, ...}}."""
    out: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        cols = rest.split()
        if len(cols) < 16:
            continue
        try:
            out[name] = {
                "rx_bytes": int(cols[0]),
                "rx_packets": int(cols[1]),
                "rx_errs": int(cols[2]),
                "rx_drop": int(cols[3]),
                "tx_bytes": int(cols[8]),
                "tx_packets": int(cols[9]),
                "tx_errs": int(cols[10]),
                "tx_drop": int(cols[11]),
            }
        except (ValueError, IndexError):
            continue
    return out


async def _run(ssh: SlateSSH, cmd: str, timeout: float = 8.0) -> str:
    """Run a probe, return stdout (or empty string on failure)."""
    try:
        r = await ssh.run(cmd, timeout=timeout)
        return r.stdout
    except SlateSSHError as exc:
        logger.info("netdiag.cmd_failed", cmd=cmd[:60], error=str(exc))
        return ""


async def collect_diag(ssh: SlateSSH) -> dict[str, Any]:
    """Run all diagnostic probes in parallel and merge into one payload.

    We don't chain with `;` because the cumulative output of all probes blows
    past any reasonable single-cmd timeout AND one failing probe would mask
    the others. Instead we fan-out 7 independent SSH channels via
    asyncio.gather — wall time = max(probe) ≈ 4-10s instead of sum (~28s).
    Per-probe failures fall back to empty data so the page still renders the
    rest.
    """
    (
        addr_out,
        link_out,
        route4_out,
        route6_out,
        rules_out,
        neigh_out,
        proc_dev_out,
        ubus_out,
    ) = await asyncio.gather(
        _run(ssh, "ip -j addr show 2>/dev/null"),
        _run(ssh, "ip -j link show 2>/dev/null"),
        # `show table all` exposes every routing table (main, local, Tailscale's 52,
        # GL.iNet multiwan tables, per-VPN tables) — without it we only see `main`.
        _run(ssh, "ip -j route show table all 2>/dev/null"),
        _run(ssh, "ip -j -6 route show table all 2>/dev/null"),
        _run(ssh, "ip -j rule 2>/dev/null"),
        _run(ssh, "ip -j neigh 2>/dev/null"),
        _run(ssh, "cat /proc/net/dev"),
        _run(ssh, "ubus call network.interface dump 2>/dev/null", timeout=15.0),
    )
    addr_raw = _safe_json_loads(addr_out) or []
    link_raw = _safe_json_loads(link_out) or []
    route4_raw = _safe_json_loads(route4_out) or []
    route6_raw = _safe_json_loads(route6_out) or []
    rules_raw = _safe_json_loads(rules_out) or []
    neigh_raw = _safe_json_loads(neigh_out) or []
    counters = _parse_proc_net_dev(proc_dev_out)
    ubus_raw = _safe_json_loads(ubus_out) or {}

    # Build a unified interface view: merge link + addr + counters + ubus.
    by_iface: dict[str, dict[str, Any]] = {}
    for entry in link_raw:
        name = entry.get("ifname")
        if not name:
            continue
        by_iface[name] = {
            "name": name,
            "index": entry.get("ifindex"),
            "operstate": entry.get("operstate"),
            "flags": entry.get("flags", []),
            "mtu": entry.get("mtu"),
            "mac": entry.get("address"),
            "master": entry.get("master"),
            "link_type": entry.get("link_type"),
            "addresses": [],
            "counters": counters.get(name),
        }
    for entry in addr_raw:
        name = entry.get("ifname")
        if not name or name not in by_iface:
            # Address on an iface we don't know about (rare) — still add.
            by_iface.setdefault(
                name, {"name": name, "addresses": [], "counters": counters.get(name)}
            )
        for addr in entry.get("addr_info") or []:
            by_iface[name]["addresses"].append(
                {
                    "family": addr.get("family"),
                    "local": addr.get("local"),
                    "prefixlen": addr.get("prefixlen"),
                    "scope": addr.get("scope"),
                    "broadcast": addr.get("broadcast"),
                    "label": addr.get("label"),
                }
            )

    # Routes — normalise structure. We KEEP the table name so the UI can
    # group/filter (a /security or /tools view that just shows "default
    # route" without knowing it lives in table 52 is misleading).
    def _norm_routes(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in raw:
            out.append(
                {
                    "dst": r.get("dst", "default"),
                    "gateway": r.get("gateway"),
                    "dev": r.get("dev"),
                    "protocol": r.get("protocol"),
                    "scope": r.get("scope"),
                    "src": r.get("prefsrc"),
                    "metric": r.get("metric"),
                    "type": r.get("type"),
                    "flags": r.get("flags", []),
                    # iproute2 emits "table" only when ≠ main; default to main
                    # so the UI can group on this field unconditionally.
                    "table": r.get("table") or "main",
                }
            )
        return out

    # Policy routing — which table is consulted for which packets.
    rules = []
    for rule in rules_raw:
        rules.append(
            {
                "priority": rule.get("priority"),
                "src": rule.get("src"),
                "dst": rule.get("dst"),
                "iif": rule.get("iif"),
                "oif": rule.get("oif"),
                "fwmark": rule.get("fwmark"),
                "table": rule.get("table"),
                "action": rule.get("action"),
                "suppress_prefixlength": rule.get("suppress_prefixlength"),
            }
        )

    # Neighbours (ARP / NDP).
    neighbours = []
    for n in neigh_raw:
        neighbours.append(
            {
                "ip": n.get("dst"),
                "dev": n.get("dev"),
                "lladdr": n.get("lladdr"),
                "state": (n.get("state") or [None])[0],
                "router": bool(n.get("router")),
            }
        )

    # UCI-level interfaces (logical names like lan/wan + proto + dns).
    ubus_interfaces = ubus_raw.get("interface") or []

    return {
        "interfaces": sorted(by_iface.values(), key=lambda x: x.get("index") or 0),
        "routes_v4": _norm_routes(route4_raw),
        "routes_v6": _norm_routes(route6_raw),
        "rules": rules,
        "neighbours": neighbours,
        "logical_interfaces": ubus_interfaces,
    }
