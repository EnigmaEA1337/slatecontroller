"""WiFi orphan detection + cleanup (Phase 2).

Phase 1 (already shipped) marks every section the agent owns with
``slate_ctrl_managed=1`` and disables anything else it doesn't
recognize. Phase 2 goes one step further : surface those orphan
sections to the operator and let them DELETE them entirely.

Why not nuke them automatically : a section without our mark might be
something the user created via SSH for a one-off experiment. We don't
own that. Operator confirmation is the right gate.

Detection rules :

  - Any ``wireless.X=wifi-iface`` section whose ``slate_ctrl_managed``
    is NOT ``1`` is candidate.
  - We also surface ``wifi-mld`` sections (MLO groups) without the mark.
  - Pure radio sections (``wireless.radio0`` etc) are NEVER touched —
    they own the PHY config, deleting them bricks the radio.

The operator gets per-section metadata so they can decide :
``section_name``, ``ssid``, ``encryption``, ``device`` (radio it's on),
``disabled`` flag, ``network`` (bridge bound to), ``managed`` (always
False for these — included for symmetry with Phase 1 reporting).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WifiOrphan:
    section: str        # uci section name (e.g. "default_radio0", "wifinet5")
    type: str           # "wifi-iface" / "wifi-mld" / …
    ssid: str
    encryption: str
    device: str         # radio (e.g. "radio0")
    network: str
    disabled: bool      # uci.disabled == "1"
    managed: bool       # always False for orphans
    extras: dict        # any other attrs we picked up — for debug display


async def list_orphans(ssh: SlateSSH) -> list[WifiOrphan]:
    """Run ``uci show wireless`` on the Slate, parse, return every
    section we don't own."""
    try:
        result = await ssh.run("uci show wireless 2>&1", timeout=10)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH read wireless config failed: {exc}") from exc

    # Parse "wireless.SECTION[.ATTR]=VALUE" lines into {section: {attr: val}}.
    sections: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("wireless."):
            continue
        try:
            key, value = line.split("=", 1)
        except ValueError:
            continue
        parts = key.split(".", 2)
        if len(parts) < 2:
            continue
        sec = parts[1]
        attr = parts[2] if len(parts) == 3 else ""
        # UCI wraps values in single quotes ; strip both layers.
        value = value.strip().strip("'").strip('"')
        sections.setdefault(sec, {})
        if attr:
            sections[sec][attr] = value
        else:
            sections[sec][".type"] = value

    orphans: list[WifiOrphan] = []
    for name, attrs in sections.items():
        sec_type = attrs.get(".type", "")
        # We only care about iface / mld sections (data-plane VAPs +
        # MLO groups) — radio sections drive the PHY and are out of
        # scope.
        if sec_type not in ("wifi-iface", "wifi-mld"):
            continue
        if attrs.get("slate_ctrl_managed") == "1":
            continue
        orphans.append(WifiOrphan(
            section=name,
            type=sec_type,
            ssid=attrs.get("ssid", ""),
            encryption=attrs.get("encryption", ""),
            device=attrs.get("device", ""),
            network=attrs.get("network", ""),
            disabled=attrs.get("disabled") == "1",
            managed=False,
            extras={
                k: v for k, v in attrs.items()
                if k not in (
                    ".type", "ssid", "encryption", "device",
                    "network", "disabled", "slate_ctrl_managed",
                )
            },
        ))
    orphans.sort(key=lambda o: (o.section, o.ssid))
    return orphans


async def delete_orphan(ssh: SlateSSH, section: str) -> bool:
    """Drop a single orphan section + commit. Refuses if the section
    actually carries our managed mark (defensive ; should never happen
    given ``list_orphans`` already filters)."""
    if not section or any(c in section for c in " ;&|`$"):
        raise ValueError(f"invalid section name {section!r}")
    quoted = shlex.quote(section)
    try:
        # Check managed mark BEFORE deleting — last-line safety.
        r = await ssh.run(
            f"uci -q get wireless.{quoted}.slate_ctrl_managed 2>/dev/null",
            timeout=5,
        )
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH safety check failed: {exc}") from exc
    if r.stdout.strip() == "1":
        raise ValueError(
            f"section {section!r} is slate_ctrl_managed=1 — "
            "refusing to delete a section the agent owns",
        )
    try:
        result = await ssh.run(
            f"uci -q delete wireless.{quoted} && "
            f"uci commit wireless && echo OK",
            timeout=10,
        )
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH delete failed: {exc}") from exc
    success = "OK" in result.stdout
    if success:
        logger.info("wifi.orphan.deleted", section=section)
    return success


async def delete_many(ssh: SlateSSH, sections: list[str]) -> dict[str, str]:
    """Delete several orphan sections in one SSH round-trip. Returns
    ``{section: "deleted" | "skipped (managed)" | "<error>"}``."""
    out: dict[str, str] = {}
    if not sections:
        return out
    # Validate first.
    for s in sections:
        if not s or any(c in s for c in " ;&|`$"):
            out[s] = "invalid name"
    valid = [s for s in sections if s not in out]
    if not valid:
        return out

    # Build a single shell pipeline : for each, check the mark first,
    # delete + report per line.
    cmd_lines = []
    for s in valid:
        q = shlex.quote(s)
        cmd_lines.append(
            f'if [ "$(uci -q get wireless.{q}.slate_ctrl_managed 2>/dev/null)" = "1" ]; then '
            f'echo "SKIP {s}"; '
            f"else uci -q delete wireless.{q} 2>/dev/null && echo \"OK {s}\" || echo \"ERR {s}\"; "
            f"fi"
        )
    cmd_lines.append("uci commit wireless")
    full_cmd = "; ".join(cmd_lines)
    try:
        result = await ssh.run(full_cmd, timeout=30)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH bulk delete failed: {exc}") from exc
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("OK "):
            out[line[3:]] = "deleted"
        elif line.startswith("SKIP "):
            out[line[5:]] = "skipped (managed)"
        elif line.startswith("ERR "):
            out[line[4:]] = "uci delete failed"
    # Mark anything we requested but didn't see in output as unknown.
    for s in valid:
        out.setdefault(s, "no response from uci")
    if any(v == "deleted" for v in out.values()):
        logger.info("wifi.orphans.bulk_deleted", count=sum(1 for v in out.values() if v == "deleted"))
    return out
