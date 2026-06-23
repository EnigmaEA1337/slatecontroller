"""Detect + install the advanced recon toolchain on the Slate.

The baseline recon engine (ping + busybox nc) ships everywhere — no
opkg install needed. Optional upgrade : ``nmap-full`` + ``arp-scan``
unlock layer-2 ARP discovery (faster + sees silent hosts) and
``-sV`` version detection (cleaner banners + service id).

This module is the single source of truth for :
- ``get_tool_status(ssh)`` : are the optional binaries present ?
- ``install_recon_tools(ssh)`` : opkg update + install (idempotent).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from app.slate.ssh import SlateSSH, SlateSSHError

# Packages we install when the operator asks for the upgrade.
# nmap-full pulls in NSE scripts which we leverage in tcp.py.
RECON_PACKAGES: tuple[str, ...] = ("nmap-full", "arp-scan", "arp-scan-database")

# Hard cap on the install timeout : an opkg run with the database
# download can take a minute or two on a slow uplink. 5 min keeps a
# stuck install from holding the SSH lock forever.
INSTALL_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class ReconToolStatus:
    """What's currently installed on the Slate."""

    has_nmap: bool
    has_arp_scan: bool
    has_gl_arp_scan: bool  # GL.iNet's pre-installed wrapper, /usr/sbin/gl-arp-scan
    nmap_version: str = ""
    arp_scan_version: str = ""
    overlay_free_mb: int = 0

    @property
    def fully_installed(self) -> bool:
        """True iff both upgrades are present."""
        return self.has_nmap and self.has_arp_scan


async def get_tool_status(ssh: SlateSSH) -> ReconToolStatus:
    """Snapshot of the optional recon toolchain on the Slate."""
    try:
        # All probes in one round-trip — keep the SSH chatter low.
        res = await ssh.run(
            "echo '=== nmap ==='; nmap --version 2>/dev/null | head -1; "
            "echo '=== arp-scan ==='; arp-scan --version 2>&1 | head -1; "
            "echo '=== gl-arp-scan ==='; test -x /usr/sbin/gl-arp-scan && echo ok; "
            "echo '=== overlay ==='; df -m /overlay 2>/dev/null | awk 'NR==2{print $4}'",
            timeout=15,
        )
    except SlateSSHError:
        return ReconToolStatus(False, False, False)
    text = res.stdout
    blocks = _split_blocks(text)
    nmap_block = blocks.get("nmap", "").strip()
    arp_block = blocks.get("arp-scan", "").strip()
    gl_block = blocks.get("gl-arp-scan", "").strip()
    overlay_block = blocks.get("overlay", "").strip()

    has_nmap = bool(nmap_block) and "Nmap version" in nmap_block
    has_arp = bool(arp_block) and arp_block.lower().startswith("arp-scan")
    has_gl_arp = gl_block == "ok"

    nmap_version = ""
    if has_nmap:
        m = re.search(r"Nmap version (\S+)", nmap_block)
        nmap_version = m.group(1) if m else ""
    arp_version = ""
    if has_arp:
        m = re.search(r"arp-scan (\S+)", arp_block)
        arp_version = m.group(1) if m else ""

    overlay_mb = 0
    try:
        overlay_mb = int(overlay_block) if overlay_block.isdigit() else 0
    except ValueError:
        pass

    return ReconToolStatus(
        has_nmap=has_nmap,
        has_arp_scan=has_arp,
        has_gl_arp_scan=has_gl_arp,
        nmap_version=nmap_version,
        arp_scan_version=arp_version,
        overlay_free_mb=overlay_mb,
    )


def _split_blocks(text: str) -> dict[str, str]:
    """Parse the combined probe output back into per-section bodies."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        marker = _block_marker(line)
        if marker is not None:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = marker
            buf = []
        else:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


_MARKER_RE = re.compile(r"^=== (.+) ===$")


def _block_marker(line: str) -> str | None:
    m = _MARKER_RE.match(line.strip())
    return m.group(1) if m else None


# ---------------------------- install ---------------------------- #


@dataclass(frozen=True)
class InstallReport:
    ok: bool
    log: str
    status: ReconToolStatus


async def install_recon_tools(ssh: SlateSSH) -> InstallReport:
    """Run ``opkg update`` + ``opkg install`` for the recon packages.

    Idempotent : opkg silently re-uses already-installed packages,
    so calling this twice is harmless. Returns the combined log so
    the operator can read what happened in the UI.
    """
    pkgs = " ".join(RECON_PACKAGES)
    cmd = (
        "echo '=== opkg update ===' ; "
        "opkg update 2>&1 | tail -20 ; "
        "echo '=== opkg install ===' ; "
        f"opkg install {pkgs} 2>&1 | tail -40 ; "
        "echo '=== rc ===' ; echo $?"
    )
    try:
        res = await asyncio.wait_for(
            ssh.run(cmd, timeout=INSTALL_TIMEOUT_S),
            timeout=INSTALL_TIMEOUT_S + 5,
        )
    except (TimeoutError, asyncio.TimeoutError) as exc:
        status = await get_tool_status(ssh)
        return InstallReport(
            ok=False,
            log=f"install timed out after {INSTALL_TIMEOUT_S}s : {exc}",
            status=status,
        )
    except SlateSSHError as exc:
        status = await get_tool_status(ssh)
        return InstallReport(ok=False, log=f"ssh error : {exc}", status=status)

    log = res.stdout
    # Extract opkg's rc from the trailer echoed by the script.
    rc_line = ""
    m = re.search(r"=== rc ===\s*\n(\d+)", log)
    if m:
        rc_line = m.group(1)
    opkg_ok = rc_line == "0"

    status = await get_tool_status(ssh)
    return InstallReport(
        ok=opkg_ok and status.fully_installed,
        log=log[:8000],  # cap log size — opkg can be chatty
        status=status,
    )
