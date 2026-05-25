"""SBOM collection from a live Slate via SSH.

Single round-trip per device: we batch all probe commands into one SSH session
to keep the load on the router minimal (collecting 585+ packages otherwise
takes 5+ s on a busy device).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import structlog

from app.security.models import Inventory, Package
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

# Packages clearly authored by GL.iNet — they have no upstream and CVE feeds
# don't cover them. Surfaced separately in the UI so users aren't misled.
_VENDOR_PREFIXES = ("gl-", "gli-", "glinet-")
_VENDOR_EXACT = {
    "1905daemon",
    "ated_ext",
    "aw-s2s",
    "adguardhome-conntrack",
    "datconf",
    "modemmanager-gl",
    "ddns-scripts-gl",
    "mwan3-gl",
}

# Sentinel between heredoc'd output blocks. Picked to be vanishingly unlikely
# in any opkg/ubus output.
_DELIM = "===SLATE-SBOM-DELIM-7f3a==="


def _strip_revision(version: str) -> str:
    """Strip the OpenWrt revision suffix to get the upstream version.

    Examples:
        "1.33.2-5"          → "1.33.2"
        "8.6.0-1"           → "8.6.0"
        "git-2026.007.09933-457a896-1" → "git-2026.007.09933-457a896"
        "20211016-1"        → "20211016"
        "2"                 → "2"
    """
    # Match the final "-N" (where N is purely digits) at end of string.
    m = re.match(r"^(.*)-\d+$", version)
    return m.group(1) if m else version


def _is_vendor(name: str) -> bool:
    return name in _VENDOR_EXACT or any(name.startswith(p) for p in _VENDOR_PREFIXES)


def _parse_opkg(stdout: str) -> list[Package]:
    """Parse the output of `opkg list-installed` (lines of `name - version`)."""
    out: list[Package] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # opkg uses " - " as the separator; some packages have hyphens in
        # their names so we split on the first " - ".
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            continue
        name, version = parts[0].strip(), parts[1].strip()
        if not name or not version:
            continue
        out.append(
            Package(
                name=name,
                version=version,
                upstream_version=_strip_revision(version),
                vendor_specific=_is_vendor(name),
            )
        )
    return out


def _parse_openwrt_release(stdout: str) -> dict[str, str]:
    """Parse /etc/openwrt_release (KEY='value' shell-style lines)."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


async def collect_inventory(
    ssh: SlateSSH,
    *,
    firmware_version: str = "",
) -> Inventory:
    """Collect a fresh SBOM from the device.

    Runs all probes in a single SSH invocation, separating outputs by a
    sentinel. Raises `SlateSSHError` if SSH itself fails — caller decides
    whether to surface as 503 or retry.

    Args:
        ssh: live SlateSSH instance.
        firmware_version: GL.iNet firmware version (e.g. "4.8.4") from the
            JSON-RPC `system.get_info` call. The SSH probes can't see it
            directly so it's passed in. Empty string if RPC was unreachable.
    """
    cmd = (
        f"cat /etc/openwrt_release; echo '{_DELIM}'; "
        f"ubus call system board; echo '{_DELIM}'; "
        f"opkg list-installed"
    )
    try:
        result = await ssh.run(cmd)
    except SlateSSHError:
        logger.warning("security.inventory.ssh_failed")
        raise
    if result.exit_status != 0:
        logger.warning(
            "security.inventory.cmd_nonzero", exit=result.exit_status, stderr=result.stderr[:200]
        )

    blocks = result.stdout.split(_DELIM)
    if len(blocks) != 3:
        # Defensive: the device returned an unexpected number of blocks.
        # Fall back to empty inventory rather than crash.
        logger.warning("security.inventory.parse_unexpected_blocks", count=len(blocks))
        blocks = blocks + [""] * (3 - len(blocks))

    release = _parse_openwrt_release(blocks[0])
    try:
        board = json.loads(blocks[1].strip())
    except json.JSONDecodeError:
        logger.warning("security.inventory.board_json_invalid")
        board = {}
    packages = _parse_opkg(blocks[2])

    return Inventory(
        taken_at=datetime.now(UTC),
        openwrt_distrib_id=release.get("DISTRIB_ID", ""),
        openwrt_release=release.get("DISTRIB_RELEASE", ""),
        openwrt_target=release.get("DISTRIB_TARGET", ""),
        openwrt_arch=release.get("DISTRIB_ARCH", ""),
        openwrt_taints=release.get("DISTRIB_TAINTS", ""),
        firmware_version=firmware_version,
        kernel=board.get("kernel", ""),
        board_name=board.get("board_name", ""),
        hostname=board.get("hostname", ""),
        model=board.get("model", ""),
        packages=packages,
    )
