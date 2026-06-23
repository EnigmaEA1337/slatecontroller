"""SSH-driven orchestration of openfortivpn on the Slate.

The controller never holds the tunnel itself — it shells over to the
Slate via the existing :class:`SlateSSH` session and asks the device to
spawn / kill / report on the openfortivpn process. This keeps the
Slate's network path local and prevents the controller from becoming a
single point of failure for VPN connectivity.

Lifecycle :

  1. **connect(slug, otp)**
       - Decrypt the password from the store.
       - SCP a tiny pidfile-aware launcher to ``/tmp/forti-<slug>.sh``.
       - Run it once : it backgrounds ``openfortivpn`` with --pppd-no-peerdns
         so DNS doesn't get hijacked, writes the PID into ``/var/run/
         forti-<slug>.pid``, and exits.
       - Poll ``ip link show`` for a ppp interface owned by that PID up
         to ~25 s. Return the iface name + tunnel IP.
  2. **disconnect()**
       - SIGTERM the pidfile contents, wait for it to exit, clean up.
  3. **status()**
       - Read the pidfile, check ``/proc/<pid>/status``, read ppp iface
         stats from ``/sys/class/net/ppp*/statistics/{rx,tx}_bytes`` and
         /proc/uptime delta.

Password handling :
  The launcher receives the password on **stdin** (NOT argv) so it's
  never visible in ``ps``. The OTP is appended to the password with the
  configurable separator (Forti default is to read OTP from a 2nd line ;
  openfortivpn's ``--otp`` flag is plumbed instead which is cleaner).
"""

from __future__ import annotations

import asyncio
import shlex
import structlog
from datetime import UTC, datetime

from app.exceptions import SlateError
from app.slate.ssh import SlateSSH, SlateSSHError
from app.vpn.fortinet.models import FortinetStatus
from app.vpn.fortinet.store import (
    FortinetConfigStore,
    FortinetNotFoundError,
    FortinetStoreError,
)


logger = structlog.get_logger(__name__)


class FortinetManagerError(SlateError):
    pass


# /tmp not /etc — the launcher + pidfile must survive across reboots
# being a no-op (we'd lose the tunnel anyway after a reboot).
PIDFILE = "/var/run/openfortivpn.pid"
LOGFILE = "/var/log/openfortivpn.log"
LAUNCHER = "/tmp/forti-launch.sh"
ACTIVE_SLUG_FILE = "/var/run/openfortivpn.slug"


def _launcher_script(
    *,
    host: str,
    port: int,
    username: str,
    trusted_cert_sha256: str,
    has_ca_file: bool,
    otp: str | None,
) -> str:
    """Generate the shell launcher pushed to the Slate.

    Password arrives on stdin (line 1). When ``otp`` is provided, it's
    passed via ``--otp`` (Forti gateways with a separate 2FA prompt).
    Most setups fold the TOTP into the password itself and leave ``otp``
    None. ``--pppd-no-peerdns`` prevents the gateway from rewriting
    resolv.conf — dnsmasq + AdGuard keep their per-network DNS logic
    intact.
    """
    args = [
        "openfortivpn",
        f"{host}:{port}",
        "-u", username,
        "--pppd-no-peerdns",
        "--pid-file", PIDFILE,
    ]
    if otp:
        args.extend(["--otp", otp])
    if trusted_cert_sha256:
        args.extend(["--trusted-cert", trusted_cert_sha256])
    if has_ca_file:
        args.extend(["--ca-file", "/tmp/forti-ca.pem"])
    cmd = " ".join(shlex.quote(a) for a in args)
    # Daemonised via setsid + nohup so the SSH session closing doesn't
    # SIGHUP the tunnel. stdin gets the password line, /var/log eats
    # output. The PID is captured BEFORE the long-running exec so we
    # can monitor it.
    return f"""#!/bin/sh
set -e
PASSWORD="$(cat)"
mkdir -p /var/run /var/log
: > {LOGFILE}
{{
  printf '%s\\n' "$PASSWORD" | setsid {cmd} >>{LOGFILE} 2>&1
}} &
echo $! > {PIDFILE}
wait $!
"""


class FortinetManager:
    """Orchestrate openfortivpn on the Slate over the existing SSH chan."""

    def __init__(
        self,
        ssh: SlateSSH,
        store: FortinetConfigStore,
    ) -> None:
        self._ssh = ssh
        self._store = store

    async def preflight(self) -> dict:
        """Check the Slate has everything needed to spawn a tunnel.

        Returns a dict ``{ok, binary, version, ppp_kmod, error}``. The
        frontend surfaces ``ok=False`` with a helpful message rather than
        letting the connect endpoint fail mid-spawn.

        We check :
          - ``openfortivpn`` in $PATH (the binary itself, must be sideloaded
            since opkg on GL.iNet 4.x doesn't ship it for aarch64),
          - kernel ``ppp`` module loaded or modprobe-able (the tunnel runs
            over ppp ; without it openfortivpn aborts on negotiation).
        """
        cmd = (
            "export PATH=/usr/sbin:/sbin:/usr/bin:/bin:$PATH ; "
            "binary=$(which openfortivpn 2>/dev/null || echo MISSING) ; "
            "version='' ; "
            "[ \"$binary\" != MISSING ] && version=$(openfortivpn --version 2>/dev/null | head -1) ; "
            "ppp=$(lsmod 2>/dev/null | grep -c '^ppp ') ; "
            "echo \"binary=$binary\" ; "
            "echo \"version=$version\" ; "
            "echo \"ppp=$ppp\""
        )
        try:
            r = await self._ssh.run(cmd, timeout=10)
        except SlateSSHError as exc:
            return {
                "ok": False,
                "binary": "",
                "version": "",
                "ppp_kmod": False,
                "error": f"SSH error: {exc}",
            }
        info = {"binary": "", "version": "", "ppp_kmod": False}
        for line in (r.stdout or "").splitlines():
            if line.startswith("binary="):
                info["binary"] = line.split("=", 1)[1].strip()
            elif line.startswith("version="):
                info["version"] = line.split("=", 1)[1].strip()
            elif line.startswith("ppp="):
                info["ppp_kmod"] = line.split("=", 1)[1].strip() != "0"
        ok = (
            info["binary"] not in ("", "MISSING")
            and "/" in info["binary"]
        )
        error = ""
        if not ok:
            error = (
                "openfortivpn binary missing on the Slate. opkg on GL.iNet "
                "4.x doesn't ship it for aarch64 — build it from the OpenWrt "
                "SDK 21.02 (target=mediatek/mt7987) and scp the binary to "
                "/usr/sbin/openfortivpn (chmod 755), then preflight again."
            )
        elif not info["ppp_kmod"]:
            # Don't fail — modprobe ppp_generic happens implicitly when the
            # pppd child fork()s. Just warn.
            error = (
                "ppp kmod not loaded — first connect will trigger modprobe ; "
                "second + should be fine."
            )
        return {"ok": ok, "error": error, **info}

    async def status(self) -> FortinetStatus:
        """Inspect the Slate for a live openfortivpn tunnel.

        Returns ``state="down"`` when nothing is running. No state lookup
        in the DB — the truth is on the Slate.
        """
        cmd = (
            f"export PATH=/usr/sbin:/sbin:/usr/bin:/bin:$PATH ; "
            f"if [ -f {PIDFILE} ] && kill -0 $(cat {PIDFILE}) 2>/dev/null ; then "
            f"  pid=$(cat {PIDFILE}) ; "
            f"  iface=$(ls /sys/class/net 2>/dev/null | grep '^ppp' | head -1) ; "
            f"  if [ -n \"$iface\" ] ; then "
            f"    rx=$(cat /sys/class/net/$iface/statistics/rx_bytes 2>/dev/null || echo 0) ; "
            f"    tx=$(cat /sys/class/net/$iface/statistics/tx_bytes 2>/dev/null || echo 0) ; "
            f"    tip=$(ip -4 -o addr show dev $iface 2>/dev/null | awk '{{print $4}}' | head -1) ; "
            f"    pip=$(ip -4 -o addr show dev $iface 2>/dev/null | awk '{{print $6}}' | head -1) ; "
            f"    slug=$(cat {ACTIVE_SLUG_FILE} 2>/dev/null) ; "
            f"    age=$(awk -v p=$pid 'BEGIN{{getline up < \"/proc/uptime\"; split(up,u,\" \"); now=u[1]}} "
            f"      NR==1{{getline ps < \"/proc/\"p\"/stat\"; split(ps,a,\" \"); started=a[22]/100; print int(now-started)}}' /proc/uptime) ; "
            f"    echo UP $iface $tip $pip $rx $tx $slug $age ; "
            f"  else "
            f"    echo CONNECTING $pid ; "
            f"  fi ; "
            f"else "
            f"  echo DOWN ; "
            f"fi"
        )
        try:
            r = await self._ssh.run(cmd, timeout=10)
        except SlateSSHError as exc:
            raise FortinetManagerError(f"SSH error reading status: {exc}") from exc
        line = (r.stdout or "").strip()
        if not line or line.startswith("DOWN"):
            return FortinetStatus(state="down")
        parts = line.split()
        if parts[0] == "CONNECTING":
            return FortinetStatus(state="connecting")
        if parts[0] == "UP" and len(parts) >= 8:
            _, iface, tunnel_ip, gw_ip, rx, tx, slug, age = parts[:8]
            return FortinetStatus(
                slug=slug or None,
                state="up",
                ppp_iface=iface,
                tunnel_ip=tunnel_ip,
                gateway_ip=gw_ip,
                rx_bytes=int(rx) if rx.isdigit() else 0,
                tx_bytes=int(tx) if tx.isdigit() else 0,
                uptime_seconds=int(age) if age.isdigit() else 0,
            )
        return FortinetStatus(state="unknown", last_error=f"unparseable: {line[:120]}")

    async def connect(
        self,
        slug: str,
        otp: str | None = None,
        *,
        username_override: str | None = None,
        password_override: str | None = None,
    ) -> FortinetStatus:
        """Establish the tunnel for ``slug`` with the operator-typed OTP.

        ``username_override`` / ``password_override`` let the mobile login
        flow type fresh creds at every connect, without ever persisting
        them. When both are ``None``, fall back to the stored config —
        the desktop CRUD path. When at least one override is supplied
        AND a stored secret is missing, the override wins ; we never
        crash for "no stored password" if the operator just typed one.

        Side effects on the Slate :
          - ``/tmp/forti-launch.sh`` (the launcher), ``/tmp/forti-ca.pem``
            (only if the config has a CA PEM)
          - ``/var/run/openfortivpn.pid``, ``/var/run/openfortivpn.slug``
          - ``/var/log/openfortivpn.log``
        """
        try:
            config = await self._store.get_by_slug(slug)
        except FortinetNotFoundError as exc:
            raise FortinetManagerError(f"config {slug!r} not found") from exc

        # Resolve the credential pair :
        #   - explicit override → use it (mobile flow, ad-hoc)
        #   - else stored password → decrypt
        #   - else neither → bail with an instructive error.
        if password_override:
            password = password_override
        else:
            if not await self._store.has_password(slug):
                raise FortinetManagerError(
                    f"config {slug!r} has no stored password and none was "
                    f"supplied in the request — type it in the login form.",
                )
            try:
                password = await self._store.get_password(slug)
            except FortinetStoreError as exc:
                raise FortinetManagerError(str(exc)) from exc
        username = username_override or config.username

        # Preflight : refuse before spawning if openfortivpn isn't on the
        # Slate. Spares the operator the timeout window + an opaque error.
        pf = await self.preflight()
        if not pf.get("ok"):
            raise FortinetManagerError(
                pf.get("error") or "openfortivpn unavailable on the Slate",
            )

        # Refuse if a tunnel is already up — the operator must disconnect
        # explicitly so they consciously acknowledge tearing the existing
        # one. Avoids the foot-gun where a fat-fingered profile click
        # silently steals the active session.
        current = await self.status()
        if current.state in ("up", "connecting"):
            raise FortinetManagerError(
                f"a Forti tunnel is already {current.state} "
                f"(slug={current.slug!r}) — disconnect first",
            )

        has_ca = bool(config.ca_cert_pem.strip())
        if has_ca:
            try:
                await self._ssh.put_bytes(
                    config.ca_cert_pem.encode(),
                    "/tmp/forti-ca.pem",
                    mode=0o600,
                )
            except SlateSSHError as exc:
                raise FortinetManagerError(f"push CA file: {exc}") from exc

        launcher = _launcher_script(
            host=config.gateway_host,
            port=config.gateway_port,
            username=username,
            trusted_cert_sha256=config.trusted_cert_sha256,
            has_ca_file=has_ca,
            otp=otp,
        )
        try:
            await self._ssh.put_bytes(launcher.encode(), LAUNCHER, mode=0o700)
            await self._ssh.run(
                f"echo {shlex.quote(slug)} > {ACTIVE_SLUG_FILE}",
                timeout=5,
            )
        except SlateSSHError as exc:
            raise FortinetManagerError(f"push launcher: {exc}") from exc

        # Spawn detached so the SSH ssh.run() doesn't block on the tunnel.
        # The launcher reads the password from stdin then daemonises via
        # setsid+wait — we feed the password on the same pipe.
        spawn = (
            f"printf '%s' {shlex.quote(password)} | "
            f"setsid sh {LAUNCHER} </dev/null >/dev/null 2>&1 &"
        )
        try:
            await self._ssh.run(spawn, timeout=10)
        except SlateSSHError as exc:
            raise FortinetManagerError(f"spawn launcher: {exc}") from exc

        # Poll up to 25 s for the ppp iface to appear AND get an IP.
        # openfortivpn typically completes auth + ppp negotiation in 3-8 s ;
        # 25 s covers slow gateways with FortiToken-push 2FA review delays.
        deadline_loops = 25
        for _ in range(deadline_loops):
            await asyncio.sleep(1)
            st = await self.status()
            if st.state == "up":
                await self._store.mark_status(
                    slug, status="up", bump_connected=True,
                )
                logger.info(
                    "forti.connected",
                    slug=slug, iface=st.ppp_iface, tunnel_ip=st.tunnel_ip,
                )
                return st
            if st.state == "failed":
                # Capture the last logfile line for the operator
                try:
                    r = await self._ssh.run(
                        f"tail -5 {LOGFILE} 2>/dev/null", timeout=5,
                    )
                    err = (r.stdout or "").strip().replace("\n", " | ")[:400]
                except SlateSSHError:
                    err = "agent SSH dropped during connect"
                await self._store.mark_status(slug, status="failed", last_error=err)
                raise FortinetManagerError(f"connect failed: {err}")

        # Timed out — leave whatever's running as-is, the operator can
        # diagnose via the logfile.
        try:
            r = await self._ssh.run(
                f"tail -10 {LOGFILE} 2>/dev/null", timeout=5,
            )
            tail = (r.stdout or "").strip().replace("\n", " | ")[:400]
        except SlateSSHError:
            tail = "(log unreachable)"
        await self._store.mark_status(
            slug, status="failed",
            last_error=f"connect timeout (25s) — last log: {tail}",
        )
        raise FortinetManagerError(
            f"connect timeout — last lines of {LOGFILE}: {tail}",
        )

    async def disconnect(self) -> FortinetStatus:
        """Kill the running tunnel (if any). No-op when already DOWN."""
        cmd = (
            f"export PATH=/usr/sbin:/sbin:/usr/bin:/bin:$PATH ; "
            f"if [ -f {PIDFILE} ] ; then "
            f"  pid=$(cat {PIDFILE}) ; "
            f"  kill -TERM $pid 2>/dev/null ; "
            f"  for i in 1 2 3 4 5 ; do "
            f"    kill -0 $pid 2>/dev/null || break ; sleep 1 ; "
            f"  done ; "
            f"  kill -KILL $pid 2>/dev/null ; "
            f"fi ; "
            f"rm -f {PIDFILE} {ACTIVE_SLUG_FILE} {LAUNCHER} /tmp/forti-ca.pem 2>/dev/null ; "
            f"echo OK"
        )
        try:
            await self._ssh.run(cmd, timeout=15)
        except SlateSSHError as exc:
            raise FortinetManagerError(f"disconnect SSH error: {exc}") from exc
        # Look up the active slug (best-effort — the file may already be gone)
        try:
            r = await self._ssh.run(
                f"cat {ACTIVE_SLUG_FILE} 2>/dev/null", timeout=5,
            )
            active = (r.stdout or "").strip()
        except SlateSSHError:
            active = ""
        if active:
            try:
                await self._store.mark_status(
                    active, status="down", bump_disconnected=True,
                )
            except FortinetStoreError:
                pass
        logger.info("forti.disconnected", slug=active or None)
        return FortinetStatus(state="down")
