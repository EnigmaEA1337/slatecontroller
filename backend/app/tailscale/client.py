"""SSH-driven wrapper around the `tailscale` CLI on the Slate.

Why CLI over the Go library:
  - The CLI is already on the firmware (gl-sdk4-tailscale, /usr/sbin/tailscale)
  - Output stable across versions via the documented --json flag
  - No need to ship Tailscale's Go SDK or talk to /var/run/tailscale/tailscaled.sock

We always run via SSH against the Slate so this works even if the controller
container can't directly reach the tailnet.
"""

from __future__ import annotations

import json
import re
import shlex
from datetime import UTC, datetime
from typing import Any

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError
from app.tailscale.models import (
    BackendState,
    TailscaleConfigInput,
    TailscalePeer,
    TailscaleStatus,
)

logger = structlog.get_logger(__name__)

# Pulled out of `tailscale up` text output when the daemon prompts for browser login.
_AUTH_URL_RE = re.compile(r"https://login\.tailscale\.com/a/[a-zA-Z0-9_-]+")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Tailscale returns "2024-01-02T03:04:05Z" or with offset
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _parse_status_json(data: dict[str, Any]) -> TailscaleStatus:
    """Reduce the verbose `tailscale status --json` to our subset."""
    backend_state: BackendState = data.get("BackendState") or "NoState"
    self_info = data.get("Self") or {}
    peers_raw = data.get("Peer") or {}

    # Routes self currently advertises (allowed by control plane).
    advertised = list((self_info.get("PrimaryRoutes") or []))

    # Did the user pass --advertise-exit-node?
    exit_offered = bool(self_info.get("ExitNodeOption")) if "ExitNodeOption" in self_info else False

    # Is some peer being used as exit node?
    use_exit_node = ""
    for p in peers_raw.values():
        if isinstance(p, dict) and p.get("ExitNode"):
            use_exit_node = p.get("HostName") or p.get("DNSName") or ""
            break

    peers: list[TailscalePeer] = []
    for p in peers_raw.values():
        if not isinstance(p, dict):
            continue
        peers.append(
            TailscalePeer(
                hostname=p.get("HostName") or "",
                dns_name=p.get("DNSName") or "",
                tailscale_ips=list(p.get("TailscaleIPs") or []),
                online=bool(p.get("Online")),
                os=p.get("OS") or "",
                user=p.get("UserID") and str(p.get("UserID")) or "",
                last_seen=_parse_iso(p.get("LastSeen")),
                primary_routes=list(p.get("PrimaryRoutes") or []),
                exit_node=bool(p.get("ExitNode")),
                exit_node_option=bool(p.get("ExitNodeOption")),
            )
        )

    # `CurrentTailnet` is `null` (not absent) in the JSON when the
    # daemon is up but the device isn't bound to a tailnet yet — the
    # "NeedsLogin" state on a freshly-installed Slate. The default-`{}`
    # form of `dict.get` doesn't help there (it's not missing, it's
    # explicitly None), so we `or {}` the value before re-indexing.
    _cur = data.get("CurrentTailnet") or {}
    tailnet = data.get("MagicDNSSuffix") or _cur.get("MagicDNSSuffix") or ""

    return TailscaleStatus(
        installed=True,
        daemon_running=True,
        backend_state=backend_state,
        hostname=self_info.get("HostName") or "",
        tailscale_ips=list(self_info.get("TailscaleIPs") or []),
        tailnet=tailnet,
        self_id=str(self_info.get("ID") or ""),
        # `accept_routes` isn't directly in status JSON — we read it from
        # NetworkMap.SelfNode when present, else default false.
        accept_routes=False,
        advertised_routes=advertised,
        exit_node_enabled=exit_offered,
        use_exit_node=use_exit_node,
        peers=peers,
    )


class TailscaleClient:
    """Drive the Slate's local Tailscale via SSH."""

    def __init__(self, ssh: SlateSSH) -> None:
        self._ssh = ssh

    async def detect_installed(self) -> bool:
        """Return True if the `tailscale` binary is reachable on the Slate."""
        try:
            r = await self._ssh.run("command -v tailscale")
        except SlateSSHError:
            return False
        return bool(r.stdout.strip()) and r.exit_status == 0

    async def get_status(self) -> TailscaleStatus:
        """Best-effort snapshot of the local Tailscale state."""
        if not await self.detect_installed():
            return TailscaleStatus(installed=False)
        # Check daemon process first — if not running, status command will
        # fail with a confusing socket error.
        try:
            ps_r = await self._ssh.run("pidof tailscaled || true")
        except SlateSSHError as exc:
            return TailscaleStatus(installed=True, error=str(exc))
        if not ps_r.stdout.strip():
            return TailscaleStatus(
                installed=True,
                daemon_running=False,
                backend_state="Stopped",
            )

        try:
            r = await self._ssh.run("tailscale status --json 2>&1")
        except SlateSSHError as exc:
            return TailscaleStatus(installed=True, daemon_running=True, error=str(exc))
        out = r.stdout
        if not out.strip().startswith("{"):
            # Not a JSON — likely "Tailscale is stopped." or similar.
            state: BackendState = "Stopped" if "stopped" in out.lower() else "NoState"
            return TailscaleStatus(
                installed=True, daemon_running=True,
                backend_state=state, error=out.strip()[:300],
            )
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            return TailscaleStatus(
                installed=True, daemon_running=True,
                error=f"unparseable JSON: {exc}",
            )
        return _parse_status_json(data)

    async def connect(self, cfg: TailscaleConfigInput) -> tuple[bool, str, str | None]:
        """Run `tailscale up` with the given options.

        Returns: (success, stdout-stderr concatenated, browser-auth URL if any).

        Order of operations:
          1. Ensure tailscaled is running (start init.d service if needed)
          2. `tailscale up` with the right flags (auth key, routes, exit-node, ...)
        """
        # 1. Make sure the daemon is up.
        # GL.iNet's init.d script reads UCI `tailscale.settings.enabled`: if
        # it's 0 (factory default), `start` is a no-op. We have to set it
        # first, commit, *then* start.
        try:
            await self._ssh.run(
                "uci set tailscale.settings.enabled=1 && uci commit tailscale"
            )
            await self._ssh.run(
                "/etc/init.d/tailscale enable; /etc/init.d/tailscale start"
            )
            # Give procd a couple of seconds to actually spawn tailscaled.
            await self._ssh.run(
                "for i in 1 2 3 4 5; do pidof tailscaled >/dev/null && exit 0; sleep 1; done; "
                "echo 'timeout waiting for tailscaled'; exit 1"
            )
        except SlateSSHError as exc:
            return False, f"failed to start tailscaled: {exc}", None

        # 2. Build the `tailscale up` command.
        parts = ["tailscale", "up", "--reset"]
        if cfg.auth_key:
            parts += [f"--authkey={shlex.quote(cfg.auth_key)}"]
        if cfg.hostname:
            parts += [f"--hostname={shlex.quote(cfg.hostname)}"]
        if cfg.accept_routes:
            parts.append("--accept-routes")
        else:
            parts.append("--accept-routes=false")
        if cfg.accept_dns:
            parts.append("--accept-dns")
        else:
            parts.append("--accept-dns=false")
        if cfg.advertise_routes:
            parts.append(f"--advertise-routes={','.join(cfg.advertise_routes)}")
        if cfg.advertise_exit_node:
            parts.append("--advertise-exit-node")
        if cfg.exit_node:
            parts.append(f"--exit-node={shlex.quote(cfg.exit_node)}")
            # Required when exit node is set, otherwise LAN routes break.
            parts.append("--exit-node-allow-lan-access=true")
        if cfg.shields_up:
            parts.append("--shields-up")
        # `tailscale up` blocks until login completes for browser-based flow.
        # Run with timeout=20s; if it's waiting for a URL we extract it.
        parts.append("--timeout=15s")

        cmd = " ".join(parts) + " 2>&1"
        try:
            r = await self._ssh.run(cmd)
        except SlateSSHError as exc:
            return False, f"SSH error: {exc}", None
        out = r.stdout

        # Browser-login URL: `tailscale up` without an auth key prints it.
        url_match = _AUTH_URL_RE.search(out)
        auth_url = url_match.group(0) if url_match else None

        if r.exit_status == 0 and not auth_url:
            return True, out.strip()[:600], None
        if auth_url:
            return False, out.strip()[:600], auth_url
        return False, out.strip()[:600], None

    async def apply_overrides(
        self,
        *,
        accept_routes: bool | None = None,
        accept_dns: bool | None = None,
        advertise_routes: list[str] | None = None,
        advertise_exit_node: bool | None = None,
        exit_node: str | None = None,
        shields_up: bool | None = None,
    ) -> tuple[bool, list[str]]:
        """Apply a partial set of prefs via `tailscale set` (no re-auth).

        Used by the profile applier — each parameter is "None = leave alone,
        value = set". Several flags collapse into one CLI invocation, which
        is both faster (~200ms vs 500ms+ per call) and atomic — either all
        the flags apply or none do.

        Returns (ok, list_of_applied_flag_descriptions). The descriptions
        feed the profile-activation audit log.
        """
        flags: list[str] = []
        applied: list[str] = []
        if accept_routes is not None:
            flags.append(f"--accept-routes={'true' if accept_routes else 'false'}")
            applied.append(f"accept_routes={accept_routes}")
        if accept_dns is not None:
            flags.append(f"--accept-dns={'true' if accept_dns else 'false'}")
            applied.append(f"accept_dns={accept_dns}")
        if advertise_routes is not None:
            joined = ",".join(advertise_routes)
            flags.append(f"--advertise-routes={joined}")
            applied.append(f"advertise_routes={joined or '(empty)'}")
        if advertise_exit_node is not None:
            flags.append(f"--advertise-exit-node={'true' if advertise_exit_node else 'false'}")
            applied.append(f"advertise_exit_node={advertise_exit_node}")
        if exit_node is not None:
            # Empty string is intentional → disables exit-node usage.
            if exit_node:
                flags.append(f"--exit-node={shlex.quote(exit_node)}")
                flags.append("--exit-node-allow-lan-access=true")
                applied.append(f"exit_node={exit_node}")
            else:
                flags.append("--exit-node=\"\"")
                applied.append("exit_node=(unset)")
        if shields_up is not None:
            flags.append(f"--shields-up={'true' if shields_up else 'false'}")
            applied.append(f"shields_up={shields_up}")
        if not flags:
            return True, []
        cmd = f"tailscale set {' '.join(flags)} 2>&1"
        try:
            r = await self._ssh.run(cmd)
        except SlateSSHError as exc:
            return False, [f"SSH error: {exc}"]
        if r.exit_status != 0:
            return False, [f"tailscale set failed: {r.stdout.strip()[:300]}"]
        return True, applied

    async def set_exit_node(self, target: str) -> tuple[bool, str]:
        """Switch the exit-node *dynamically* (no `tailscale up --reset`).

        `tailscale set --exit-node=<X>` updates prefs in-place without
        re-running the full daemon negotiation. Pass an empty string to
        disable exit-node routing entirely.

        Returns (ok, stdout-stderr). The HA watchdog calls this repeatedly
        with different targets when a peer drops out — keeping it cheap is
        important.
        """
        safe = shlex.quote(target) if target else "\"\""
        # `--exit-node-allow-lan-access` defaults to false on `set` — we keep
        # it true to mirror the value used in `up`.
        flag = "--exit-node-allow-lan-access=true" if target else ""
        cmd = f"tailscale set --exit-node={safe} {flag} 2>&1"
        try:
            r = await self._ssh.run(cmd)
        except SlateSSHError as exc:
            return False, f"SSH error: {exc}"
        return r.exit_status == 0, r.stdout.strip()[:400]

    async def disconnect(self) -> tuple[bool, str]:
        """`tailscale down` — keeps daemon running but disconnects from tailnet."""
        try:
            r = await self._ssh.run("tailscale down 2>&1")
        except SlateSSHError as exc:
            return False, f"SSH error: {exc}"
        return r.exit_status == 0, r.stdout.strip()[:400]

    async def logout(self) -> tuple[bool, str]:
        """Wipe the device identity (force re-auth next connect)."""
        try:
            r = await self._ssh.run("tailscale logout 2>&1")
        except SlateSSHError as exc:
            return False, f"SSH error: {exc}"
        return r.exit_status == 0, r.stdout.strip()[:400]

    async def traceroute(
        self, target: str, max_hops: int = 15
    ) -> tuple[bool, str]:
        """Trace the L3 path from the Slate to `target`.

        Uses busybox-style traceroute (`-m` max hops, `-w` wait, `-q` 1 probe
        for speed). Caller can pass any hostname/IP — quoted via shlex.

        Worst-case wall time: `max_hops * 2s * 1 probe`. We budget a SSH
        timeout of `max_hops*2 + 5s`.
        """
        max_hops = max(1, min(int(max_hops), 30))
        safe = shlex.quote(target)
        cmd = f"traceroute -m {max_hops} -w 2 -q 1 -n {safe} 2>&1"
        try:
            r = await self._ssh.run(cmd, timeout=float(max_hops * 2 + 5))
        except SlateSSHError as exc:
            return False, f"SSH error: {exc}"
        return r.exit_status == 0, r.stdout.strip()[:8000]

    async def ping(
        self, target: str, mode: str = "icmp", count: int = 3
    ) -> tuple[bool, str]:
        """Run a ping test from the Slate.

        Args:
            target: hostname or IP. Quoted with shlex so a malicious value
                can't break out of the shell.
            mode: "icmp" (regular kernel ping) or "tailscale" (overlay ping
                that reports direct vs DERP relay path).
            count: number of probes (1..10, clamped).

        Returns: (ok, raw_output). `ok` is True iff exit_status == 0.
        """
        count = max(1, min(int(count), 10))
        safe_target = shlex.quote(target)
        if mode == "tailscale":
            cmd = f"tailscale ping -c {count} {safe_target} 2>&1"
        else:
            # busybox ping on OpenWrt: -W is timeout per probe (seconds).
            cmd = f"ping -c {count} -W 2 {safe_target} 2>&1"
        try:
            r = await self._ssh.run(cmd, timeout=max(10.0, count * 3.0))
        except SlateSSHError as exc:
            return False, f"SSH error: {exc}"
        return r.exit_status == 0, r.stdout.strip()[:4000]
