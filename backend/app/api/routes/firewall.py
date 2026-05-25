"""Read-only firewall introspection — every UCI section on the Slate.

Parses `uci show firewall` once per request and returns a structured
view : zones (with input/output/forward policies), rules (config rule),
forwardings (config forwarding, src→dest), redirects (port forwards),
includes (firewall.user + custom paths), defaults.

Each rule is tagged by `origin` so the UI can colour them appropriately:

  - ``slate-ctrl``   : we created it (section id starts with `SC_FR_`)
  - ``gl-inet``     : ships with the GL.iNet firmware (heuristic on
                      section id : `lan_drop_leaked_*`, `wan_drop_leaked_*`,
                      `wgserver_*`, `ovpnserver_*`, etc.)
  - ``openwrt``     : stock OpenWrt rule (named `Allow-DHCP-Renew`,
                      `Allow-IGMP`, `Support-UDP-Traceroute`, …)
  - ``user``        : everything else (custom, hand-added)

V1 is read-only. No mutation here ; toggles + adds happen via the
existing per-subsystem endpoints (anti-bypass, agent apply, etc.) or
direct UCI edits.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_slate_ssh
from app.auth import User, get_current_user
from app.firewall.rule_names import PREFIX as SC_FR_PREFIX
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/firewall", tags=["firewall"])


Origin = Literal["slate-ctrl", "gl-inet", "openwrt", "user"]


# Heuristic prefixes for GL.iNet firmware-bundled rules. Anything else
# without an explicit "Allow-" or our SC_FR_ prefix falls back to "user".
_GLINET_PREFIXES = (
    "lan_drop_leaked", "lan_drop_leak",
    "wan_drop_leaked", "wan_drop_leak",
    "guest_drop_leaked", "guest_drop_leak",
    "wgserver_drop_leaked", "wgserver_drop_leak",
    "ovpnserver_drop_leaked", "ovpnserver_drop_leak",
    "wgclient",
    "gl_",
)


class FwAddress(BaseModel):
    family: str | None = None
    local: str | None = None
    prefixlen: int | None = None


class FwZone(BaseModel):
    name: str
    section_id: str
    input: str | None
    output: str | None
    forward: str | None
    networks: list[str] = []
    masq: bool = False


class FwRule(BaseModel):
    section_id: str
    name: str | None
    src: str | None
    dest: str | None
    proto: str | list[str] | None
    src_port: str | None
    dest_port: str | None
    src_ip: str | None
    dest_ip: str | None
    target: str | None
    family: str | None
    enabled: bool
    origin: Origin
    raw: dict[str, str] = {}


class FwForwarding(BaseModel):
    section_id: str
    name: str | None
    src: str | None
    dest: str | None
    enabled: bool
    origin: Origin


class FwInclude(BaseModel):
    section_id: str
    name: str | None
    path: str | None


class FwDefaults(BaseModel):
    input: str | None
    output: str | None
    forward: str | None
    syn_flood: bool | None
    drop_invalid: bool | None


class FirewallSnapshot(BaseModel):
    defaults: FwDefaults | None
    zones: list[FwZone]
    rules: list[FwRule]
    forwardings: list[FwForwarding]
    includes: list[FwInclude]
    # Convenience counters for the UI header.
    counts: dict[str, int]


def _classify(section_id: str, name: str | None) -> Origin:
    if section_id.startswith(SC_FR_PREFIX):
        return "slate-ctrl"
    sid_lower = section_id.lower()
    for p in _GLINET_PREFIXES:
        if sid_lower.startswith(p):
            return "gl-inet"
    # Stock OpenWrt rules typically use anonymous sections (@rule[N])
    # named "Allow-X" / "Support-X". Names with spaces or "Allow-" /
    # "Support-" prefix → openwrt.
    if name and (name.startswith(("Allow-", "Support-", "Reject-", "Block-")) or " " in name):
        return "openwrt"
    return "user"


def _parse_uci_show(text: str) -> dict[str, dict[str, dict[str, str]]]:
    """Parse `uci show firewall` into nested dicts.

    Output shape :
      {
        "firewall": {
          "<section_id>": {
            "__type__": "<config type>",
            "<option>": "<value>",
            ...
          },
          ...
        }
      }

    `uci show` lines look like :
      firewall.lan_drop_leaked_dns=rule
      firewall.lan_drop_leaked_dns.name='lan_drop_leaked_dns'
      firewall.lan_drop_leaked_dns.src='lan'

    Anonymous sections are emitted as `firewall.@rule[5]=rule` — we
    keep them as-is in the dict key.
    """
    sections: dict[str, dict[str, dict[str, str]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        parts = key.split(".")
        if len(parts) < 2:
            continue
        config = parts[0]
        section_id = parts[1]
        sections.setdefault(config, {}).setdefault(section_id, {})
        if len(parts) == 2:
            # The "this section is of type X" line.
            sections[config][section_id]["__type__"] = value
        elif len(parts) == 3:
            option = parts[2]
            sections[config][section_id][option] = value
    return sections


def _bool_uci(value: str | None) -> bool:
    """Map UCI's many "truthy" string forms to bool. Default ENABLED
    when option is missing — that's how OpenWrt's firewall behaves :
    sections without `option enabled` are active."""
    if value is None:
        return True
    return value.lower() in ("1", "yes", "true", "on", "enabled")


def _list_uci(value: str | None) -> list[str]:
    """UCI `list` options come back as space-separated strings."""
    if not value:
        return []
    return value.split()


@router.get("", response_model=FirewallSnapshot)
async def get_firewall_snapshot(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FirewallSnapshot:
    """Read-only dump of every UCI firewall section on the Slate."""
    try:
        r = await ssh.run("uci show firewall 2>/dev/null", timeout=10)
    except SlateSSHError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH uci show firewall failed: {exc}",
        ) from exc

    parsed = _parse_uci_show(r.stdout).get("firewall", {})

    defaults: FwDefaults | None = None
    zones: list[FwZone] = []
    rules: list[FwRule] = []
    forwardings: list[FwForwarding] = []
    includes: list[FwInclude] = []

    for section_id, options in parsed.items():
        section_type = options.get("__type__", "")
        if section_type == "defaults":
            defaults = FwDefaults(
                input=options.get("input"),
                output=options.get("output"),
                forward=options.get("forward"),
                syn_flood=_bool_uci(options.get("syn_flood"))
                if "syn_flood" in options else None,
                drop_invalid=_bool_uci(options.get("drop_invalid"))
                if "drop_invalid" in options else None,
            )
        elif section_type == "zone":
            zones.append(FwZone(
                name=options.get("name") or section_id,
                section_id=section_id,
                input=options.get("input"),
                output=options.get("output"),
                forward=options.get("forward"),
                networks=_list_uci(options.get("network")),
                masq=_bool_uci(options.get("masq")) if "masq" in options else False,
            ))
        elif section_type == "rule":
            name = options.get("name")
            origin = _classify(section_id, name)
            rules.append(FwRule(
                section_id=section_id,
                name=name,
                src=options.get("src"),
                dest=options.get("dest"),
                proto=options.get("proto"),
                src_port=options.get("src_port"),
                dest_port=options.get("dest_port"),
                src_ip=options.get("src_ip"),
                dest_ip=options.get("dest_ip"),
                target=options.get("target"),
                family=options.get("family"),
                enabled=_bool_uci(options.get("enabled")),
                origin=origin,
                raw={k: v for k, v in options.items() if k != "__type__"},
            ))
        elif section_type == "forwarding":
            name = options.get("name")
            origin = _classify(section_id, name)
            forwardings.append(FwForwarding(
                section_id=section_id,
                name=name,
                src=options.get("src"),
                dest=options.get("dest"),
                enabled=_bool_uci(options.get("enabled")),
                origin=origin,
            ))
        elif section_type == "include":
            includes.append(FwInclude(
                section_id=section_id,
                name=options.get("name"),
                path=options.get("path"),
            ))

    # Stable ordering : zones by name, rules by (origin, name/section)
    # so SC_FR_* group together at the top.
    zones.sort(key=lambda z: z.name)
    origin_order = {"slate-ctrl": 0, "gl-inet": 1, "openwrt": 2, "user": 3}
    rules.sort(
        key=lambda r: (
            origin_order.get(r.origin, 9),
            (r.name or r.section_id).lower(),
        ),
    )
    forwardings.sort(key=lambda f: (origin_order.get(f.origin, 9), f.section_id))

    counts = {
        "zones": len(zones),
        "rules_total": len(rules),
        "rules_enabled": sum(1 for x in rules if x.enabled),
        "rules_slate_ctrl": sum(1 for x in rules if x.origin == "slate-ctrl"),
        "rules_gl_inet": sum(1 for x in rules if x.origin == "gl-inet"),
        "rules_openwrt": sum(1 for x in rules if x.origin == "openwrt"),
        "rules_user": sum(1 for x in rules if x.origin == "user"),
        "forwardings": len(forwardings),
        "includes": len(includes),
    }

    return FirewallSnapshot(
        defaults=defaults,
        zones=zones,
        rules=rules,
        forwardings=forwardings,
        includes=includes,
        counts=counts,
    )
