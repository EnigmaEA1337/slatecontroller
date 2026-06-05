"""Adoption orchestrator — the 4 hardening tasks run after device add.

Each task is independent and reports a status. A task failing doesn't block
the others (we report `partial` at the end). The orchestrator is synchronous
(< 30s typical); we don't need a background queue for now.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from app.config import Settings
from app.devices.models import AdoptionOptions, AdoptionRunReport, AdoptionTaskReport
from app.devices.store import DeviceStore
from app.devices.tls import fetch_cert
from app.settings.ssh_keys import SSHKeypairStore
from app.slate.client import SlateClient
from app.slate.ssh import SlateSSH, SlateSSHError
from app.slate_agent.deploy import deploy_agent
from app.wifi.store import WifiSsidStore

logger = structlog.get_logger(__name__)


class _TaskCtx:
    """Mutable per-task report builder."""

    def __init__(self, name: str) -> None:
        self.report = AdoptionTaskReport(
            name=name, status="pending", message="", started_at=None, finished_at=None,
        )

    def start(self) -> None:
        self.report.status = "running"
        self.report.started_at = datetime.now(UTC)

    def ok(self, message: str = "") -> AdoptionTaskReport:
        self.report.status = "ok"
        self.report.message = message
        self.report.finished_at = datetime.now(UTC)
        return self.report

    def skipped(self, message: str) -> AdoptionTaskReport:
        self.report.status = "skipped"
        self.report.message = message
        self.report.finished_at = datetime.now(UTC)
        return self.report

    def failed(self, message: str) -> AdoptionTaskReport:
        self.report.status = "failed"
        self.report.message = message
        self.report.finished_at = datetime.now(UTC)
        return self.report


async def _task_ensure_ssh_access(
    *,
    ssh: SlateSSH,
    keypair_store: SSHKeypairStore,
    device_slug: str,
) -> AdoptionTaskReport:
    """Verify the SSH channel works ; recover if a stored key no longer matches.

    Post factory-reset / re-flash on a Slate, ``/etc/dropbear/authorized_keys``
    is wiped while the controller's DB still has the keypair marked as
    deployed. The DeviceConnectionsRegistry builds the channel in
    ``auth_mode=key`` (because the keypair exists in DB), but the key is
    no longer accepted by the live Slate — every downstream task then
    fails with "Permission denied for user root".

    This task runs first and self-heals :
      1. Try a trivial ``echo`` over the current channel.
      2. If OK → channel is fine, no-op.
      3. If permission denied AND we're in key mode AND a keypair exists →
         switch to password auth, re-push the public key to
         ``/etc/dropbear/authorized_keys``, then switch back to key.
      4. If we're already in password mode (no keypair yet) → also OK,
         later tasks will work as long as the password is correct.

    Hardcoded (not in AdoptionOptions) like enable_luci / lock_wan_admin —
    it's a recovery primitive every adoption needs, not a toggle.
    """
    ctx = _TaskCtx("Ensure SSH access")
    ctx.start()

    # 1. Probe : does the current channel work ?
    try:
        await ssh.run("echo OK_PROBE", timeout=8)
        return ctx.ok(f"channel ready ({ssh.auth_mode} auth)")
    except SlateSSHError as exc:
        # Only attempt self-heal when the failure looks like an auth one.
        if "Permission denied" not in str(exc):
            return ctx.failed(f"channel broken : {exc}")
        logger.info(
            "adoption.ensure_ssh.recovery_start",
            device=device_slug, current_auth=ssh.auth_mode,
        )

    # 2. Self-heal : need a keypair in DB + currently in key mode.
    status_ = await keypair_store.get_status(device_slug)
    if not status_.generated or not status_.public_openssh:
        return ctx.failed(
            "permission denied AND no keypair in DB — "
            "check the admin password in .env or generate a keypair first",
        )

    # 3. Drop the broken key auth, retry over password.
    try:
        await ssh.use_private_key(None)  # switch to password mode
        await ssh.run("echo OK_PASSWORD", timeout=8)
    except SlateSSHError as exc:
        # Password also fails ⇒ wrong admin password or SSH locked down.
        return ctx.failed(
            "key auth failed AND password fallback failed — "
            "check admin password or root SSH config",
        )

    # 4. Re-push the public key under password auth.
    pub = status_.public_openssh.strip()
    escaped = pub.replace("'", "'\\''")
    push_cmd = (
        f"mkdir -p /etc/dropbear && touch /etc/dropbear/authorized_keys && "
        f"grep -qF '{escaped}' /etc/dropbear/authorized_keys || "
        f"echo '{escaped}' >> /etc/dropbear/authorized_keys && "
        f"chmod 600 /etc/dropbear/authorized_keys && echo OK"
    )
    try:
        result = await ssh.run(push_cmd, timeout=10)
    except SlateSSHError as exc:
        return ctx.failed(f"push of pubkey failed : {exc}")
    if "OK" not in result.stdout:
        return ctx.failed(f"authorized_keys write returned : {result.stdout!r}")

    # 5. Swap back to key mode + verify.
    pem = await keypair_store.get_private_pem(device_slug)
    if pem is None:
        return ctx.failed(
            "pubkey pushed but private key missing in DB — keypair corrupted ?",
        )
    await ssh.use_private_key(pem)
    try:
        await ssh.run("echo OK_KEY", timeout=8)
    except SlateSSHError as exc:
        return ctx.failed(f"key auth still fails after push : {exc}")

    await keypair_store.mark_deployed(device_slug)
    return ctx.ok(
        "channel recovered : pubkey re-pushed, switched back to key auth",
    )


async def _task_pin_tls(
    *,
    device_slug: str,
    host: str,
    port: int,
    store: DeviceStore,
) -> AdoptionTaskReport:
    ctx = _TaskCtx("TLS pinning")
    ctx.start()
    try:
        info = await fetch_cert(host, port)
    except Exception as exc:  # noqa: BLE001 — surface any network/SSL error
        return ctx.failed(f"could not fetch cert: {exc}")
    await store.mark_probed(
        device_slug,
        status="pending",  # adoption not done yet — final status set later
        tls_fingerprint_sha256=info.fingerprint_sha256,
    )
    return ctx.ok(
        f"pinned {info.fingerprint_sha256[:23]}… (CN={info.subject})",
    )


async def _task_force_https(
    *,
    ssh: SlateSSH,
    slate: "SlateClient | None" = None,
) -> AdoptionTaskReport:
    """Force HTTPS for the web UI.

    Two paths :

    * **JSON-RPC** (preferred, used from `/hardening/fix`) — calls
      `local-access set_config { redirect_https: True }`. On GL.iNet
      firmware 4.x the `system set_security_policy` setter from older
      docs was retired ; the daemon's writable source of truth for the
      `redirect_https` field (which `system get_security_policy` reads
      back, and which the hardening audit checks) lives in the
      `local-access` module. The value type is **boolean** — passing
      `1` returns `-32602 invalid redirect_https`.
    * **SSH/uci fallback** (used during initial adoption, before any
      JSON-RPC session exists) — sets `uhttpd.main.redirect_https=1`
      and `glconfig.general.webui_redirect=on` then reloads uhttpd.
      These uci keys aren't observed by `local-access get_config` (the
      daemon caches its own state) so the JSON-RPC path above is
      required for the audit to flip green — but the uci values are
      still useful at the OpenWrt layer (uhttpd does redirect on its
      own port).
    """
    ctx = _TaskCtx("Force HTTPS web UI")
    ctx.start()
    if slate is not None:
        try:
            await slate.call(
                "local-access",
                "set_config",
                {"redirect_https": True},
            )
            return ctx.ok(
                "JSON-RPC local-access set_config(redirect_https=true)"
            )
        except Exception as exc:  # noqa: BLE001 — fall through to ssh fallback
            logger.warning(
                "adoption.force_https.rpc_failed",
                error=str(exc),
                fallback="ssh+uci",
            )
    cmd = (
        "uci set glconfig.general.webui_redirect='on' 2>/dev/null; "
        "uci set uhttpd.main.redirect_https='1' 2>/dev/null; "
        "uci commit glconfig 2>/dev/null; "
        "uci commit uhttpd 2>/dev/null; "
        "/etc/init.d/uhttpd reload 2>/dev/null; "
        "echo OK"
    )
    try:
        result = await ssh.run(cmd)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH failed: {exc}")
    if "OK" not in result.stdout:
        return ctx.failed(f"unexpected output: {result.stdout!r} {result.stderr!r}")
    return ctx.ok("uhttpd reloaded with redirect_https=1 (uci fallback)")


async def _task_ssh_key_only(
    *,
    ssh: SlateSSH,
    keypair_store: SSHKeypairStore,
    device_slug: str,
) -> AdoptionTaskReport:
    """Ensure SSH key auth is deployed AND password auth is off."""
    ctx = _TaskCtx("SSH key-only auth")
    ctx.start()
    status = await keypair_store.get_status(device_slug)
    if not status.generated:
        # We could auto-generate + deploy, but that's destructive (changes
        # the key on the Slate). Safer to bail and surface a clear message.
        return ctx.skipped(
            "no keypair generated yet — visit Settings → SSH keypair first",
        )
    if not status.deployed_to_slate:
        return ctx.skipped(
            "keypair generated but not deployed — visit Settings → SSH keypair",
        )
    # Disable password auth on dropbear.
    cmd = (
        "uci set dropbear.@dropbear[0].PasswordAuth=off && "
        "uci commit dropbear && "
        "/etc/init.d/dropbear restart && echo OK"
    )
    try:
        result = await ssh.run(cmd)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH failed: {exc}")
    if "OK" not in result.stdout:
        return ctx.failed(f"unexpected output: {result.stdout!r}")
    return ctx.ok("dropbear PasswordAuth=off, restarted")


async def _task_enable_luci(*, ssh: SlateSSH) -> AdoptionTaskReport:
    """Install + enable the LuCI advanced web UI on the Slate.

    GL.iNet's stock firmware on the Slate 7 Pro does NOT ship LuCI
    pre-installed — the GL.iNet UI has an "Install Now" button on
    its Advanced Settings page that runs ``opkg install luci``. Only
    a handful of compat-libs (``luci-app-mtk``, ``luci-lib-nixio``)
    are present out of the box.

    This task replicates that install + flips the access toggles :
       1. Check if LuCI is already installed (``luci-base`` in opkg).
       2. If not, ``opkg update && opkg install luci`` over the
          existing WAN uplink. Pulls ~5 MB of packages, ~30-60 s.
       3. Flip ``glconfig.luci_main.luci_enable=1`` and
          ``glconfig.general.luci_access=1`` so the GL.iNet UI's
          Advanced Settings page stops showing the "Install Now"
          button and ``/cgi-bin/luci`` is reachable on uhttpd.

    LuCI auth uses the root password (same as the GL.iNet admin
    password by default), so no extra credential setup needed.

    Failure modes :
       - Slate has no internet uplink (opkg update fails) → task
         reports failed with the upstream error
       - opkg lock contention (rare, during firmware boot) → retry
         on the next adoption run
    """
    ctx = _TaskCtx("Enable LuCI")
    ctx.start()
    # Single shell script : check, install, flip, reload — all in one
    # round-trip. Heredoc with `set -e` so a failed step is reflected
    # in the exit status, but we capture stdout for diagnostics either
    # way. The ``opkg update`` is only run if luci-base is missing
    # to avoid the ~5s latency on every adoption.
    # The GL.iNet UI's "Install Now" button installs ``gl-sdk4-luci``,
    # NOT just upstream ``luci``. The gl-sdk4-luci package :
    #   - depends on ``luci`` (pulls the full OpenWrt web UI)
    #   - creates /etc/config/luci (which GL.iNet's UI keys off to know
    #     LuCI is installed)
    #   - opens uhttpd from 127.0.0.1 → 0.0.0.0 (LAN-reachable)
    #   - tweaks nginx so the front-facing :443 routes /cgi-bin/luci
    # Just installing ``luci`` alone leaves the UI showing "Install Now"
    # forever — live-confirmed on Slate 7 Pro firmware 4.8.4.
    cmd = r"""
        if opkg list-installed 2>/dev/null | grep -q '^gl-sdk4-luci '; then
            echo "LUCI_PRESENT=1"
        else
            echo "LUCI_INSTALL=start"
            opkg update >/dev/null 2>&1 || { echo "OPKG_UPDATE_FAILED"; exit 1; }
            opkg install gl-sdk4-luci >/tmp/slate-ctrl-luci-install.log 2>&1 \
                || { echo "OPKG_INSTALL_FAILED"; tail -3 /tmp/slate-ctrl-luci-install.log; exit 1; }
            echo "LUCI_INSTALL=done"
        fi
        uci -q set glconfig.luci_main='glconfig' 2>/dev/null
        uci -q set glconfig.luci_main.luci_enable='1' 2>/dev/null
        uci -q set glconfig.general.luci_access='1' 2>/dev/null
        uci -q commit glconfig 2>/dev/null
        /etc/init.d/uhttpd reload 2>/dev/null
        /etc/init.d/nginx reload 2>/dev/null
        echo OK
    """
    try:
        # opkg install can take 30-60s on slow uplinks (hotel WiFi).
        result = await ssh.run(cmd, timeout=180)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH failed: {exc}")
    if "OPKG_UPDATE_FAILED" in result.stdout:
        return ctx.failed("opkg update failed — check WAN uplink")
    if "OPKG_INSTALL_FAILED" in result.stdout:
        # Surface the tail of the install log for diagnostics.
        return ctx.failed(
            f"opkg install luci failed : {result.stdout.strip()}",
        )
    if "OK" not in result.stdout:
        return ctx.failed(f"unexpected output: {result.stdout!r}")
    # GL.iNet 4.x firmware exposes LuCI on uhttpd's own :8443 port
    # (separate from nginx on :443 which serves the GL.iNet UI). Surface
    # the exact URL so the operator doesn't guess.
    luci_url_hint = "https://<slate-ip>:8443/cgi-bin/luci/  (root + admin password)"
    if "LUCI_INSTALL=done" in result.stdout:
        return ctx.ok(f"LuCI installed + enabled → {luci_url_hint}")
    return ctx.ok(f"LuCI already installed → {luci_url_hint}")


async def _task_lock_wan_admin(*, ssh: SlateSSH) -> AdoptionTaskReport:
    """Lock down the WAN-side admin surface : no ping, no HTTPS, no SSH.

    GL.iNet's stock UI exposes three toggles under "Remote Access Control" :
       - Allow Ping from WAN
       - HTTPS Remote Access
       - SSH Remote Access
    They all default to OFF on a fresh install but operators sometimes
    flip them on for troubleshooting. We force them OFF at adoption so a
    re-adoption is the canonical "back to safe defaults" path.

    Two-layer strategy :

    1. Flip every known GL.iNet UCI flag that gates the three toggles.
       Names vary across firmware revs (4.5 used ``glconfig.general.*``,
       4.6+ moved to ``glconfig.remote_access.*``). We try all known
       names with ``uci -q`` so missing ones are silent no-ops.

    2. Belt-and-braces : write explicit firewall rules
       ``SC_FR_HD_WAN_{PING,HTTPS,SSH}_DROP`` that REJECT inbound from the
       WAN zone on TCP/22, TCP/443 and ICMP echo-request. This guarantees
       the lockdown holds even if GL.iNet adds a new toggle path in a
       future firmware that we don't know about yet.

    Reload uhttpd + dropbear + firewall so all three services re-apply
    immediately, without waiting for the next reboot.
    """
    ctx = _TaskCtx("Lock WAN admin (ping/HTTPS/SSH)")
    ctx.start()
    cmd = r"""
        # 1. GL.iNet UCI flags — best-effort across firmware revs.
        for path in \
            glconfig.general.wan_ping \
            glconfig.general.https_remote_access \
            glconfig.general.ssh_remote_access \
            glconfig.remote_access.ping \
            glconfig.remote_access.https \
            glconfig.remote_access.ssh \
            glconfig.remote_access.wan_ping ; do
            uci -q set "$path=0" 2>/dev/null
        done
        # Some firmwares use "off" instead of "0" for these toggles.
        uci -q set glconfig.general.wan_ping='off' 2>/dev/null
        uci -q commit glconfig 2>/dev/null

        # 1b. The single flag that DRIVES `system.get_security_policy`
        # over JSON-RPC (and therefore the hardening check verdict).
        # Discovered live 2026-06-05 : the controller's hardening check
        # reads this via RPC, so the entire "Admin UI restreinte au LAN"
        # status depends on it. Without this line, every other lockdown
        # below is invisible to the verifier.
        uci -q set oui-httpd.main.security_rule='1' 2>/dev/null
        uci -q commit oui-httpd 2>/dev/null
        /etc/init.d/oui-httpd reload 2>/dev/null || \
            /etc/init.d/oui-httpd restart 2>/dev/null

        # 2. Belt-and-braces firewall rules with our SC_FR_HD_* namespace.
        # Idempotent upsert via uci set ; option slate_ctrl_managed=1 marks
        # them as ours for any future orphan purge.
        _sc_rule () {
            local name="$1"; shift
            uci -q get "firewall.$name" >/dev/null 2>&1 \
                || uci set "firewall.$name=rule"
            uci set "firewall.$name.name=$name"
            uci set "firewall.$name.enabled=1"
            uci set "firewall.$name.src=wan"
            uci set "firewall.$name.target=REJECT"
            uci set "firewall.$name.slate_ctrl_managed=1"
            while [ "$#" -ge 2 ]; do
                uci set "firewall.$name.$1=$2"
                shift 2
            done
        }
        _sc_rule SC_FR_HD_WAN_PING_DROP \
            proto icmp icmp_type echo-request family ipv4
        _sc_rule SC_FR_HD_WAN_HTTPS_DROP \
            proto tcp dest_port 443
        _sc_rule SC_FR_HD_WAN_SSH_DROP \
            proto tcp dest_port 22
        uci commit firewall

        # 3. Reload affected services.
        /etc/init.d/uhttpd reload 2>/dev/null
        /etc/init.d/dropbear reload 2>/dev/null
        if command -v fw3 >/dev/null 2>&1; then
            fw3 reload 2>/dev/null
        else
            /etc/init.d/firewall reload 2>/dev/null
        fi
        echo OK
    """
    try:
        result = await ssh.run(cmd)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH failed: {exc}")
    if "OK" not in result.stdout:
        return ctx.failed(f"unexpected output: {result.stdout!r}")
    return ctx.ok("WAN ping/HTTPS/SSH locked (GL flags + SC_FR_HD_* rules)")


async def _task_deploy_agent(
    *,
    ssh: SlateSSH,
    settings: Settings,
    wifi_store: WifiSsidStore,
) -> AdoptionTaskReport:
    """Push the slate-ctrl agent + handlers + secrets to the Slate.

    Adoption = "this Slate is mine, drive it with the controller", so
    the agent should land automatically — operator shouldn't have to go
    visit Settings → Agent → Deploy after every (re-)adoption. We
    assemble the same credentials bundle as the standalone
    ``/api/agent/deploy`` route (AdGuard admin pair + per-SSID PSKs)
    so the deployment is functionally identical, just folded into the
    adoption pipeline.

    Hardcoded (not in AdoptionOptions) : same baseline policy as
    ``_task_enable_luci`` and ``_task_lock_wan_admin`` — non-negotiable
    parts of "this is now a managed Slate".
    """
    ctx = _TaskCtx("Deploy slate-ctrl agent")
    ctx.start()

    # AdGuard creds : same placeholder-rejection rule as the standalone
    # route so we don't ship "change-me" to the Slate's secrets file.
    adguard_creds: tuple[str, str] | None = None
    if (
        settings.admin_password
        and settings.admin_password.strip().lower()
        not in {"change-me", "changeme", "password"}
    ):
        adguard_creds = (settings.admin_username, settings.admin_password)

    # Per-SSID PSKs : best-effort, individual decrypt failures don't
    # block the whole adoption.
    wifi_psks: dict[str, str] = {}
    try:
        for entry in await wifi_store.list_all():
            if not entry.has_password:
                continue
            try:
                wifi_psks[entry.slug] = await wifi_store.get_password(entry.slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "adoption.deploy_agent.wifi_psk_skip",
                    slug=entry.slug, error=str(exc),
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("adoption.deploy_agent.wifi_list_failed", error=str(exc))

    try:
        report = await deploy_agent(
            ssh,
            adguard_credentials=adguard_creds,
            wifi_passwords=wifi_psks or None,
        )
    except Exception as exc:  # noqa: BLE001 — surface as a clean fail
        return ctx.failed(f"deploy_agent crashed: {exc}")

    if not report.ok:
        # Some sub-pushes failed (e.g. couldn't write a handler). Report
        # partial : we still want the overall adoption to be 'partial'
        # rather than 'failed' because other tasks may have succeeded.
        return ctx.failed(
            f"deploy_agent had errors : {'; '.join(report.errors)}",
        )
    return ctx.ok(
        f"pushed {len(report.pushed)} artefact(s) "
        f"(handlers, secrets, scripts, hooks)",
    )


async def _task_enable_adguard(
    *, ssh: SlateSSH, settings: Settings,
) -> AdoptionTaskReport:
    """Enable AdGuard Home + provision the controller's admin credentials.

    GL.iNet ships AdGuard Home installed but DISABLED after a factory
    reset (``adguardhome.config.enabled='0'``, port 3000 closed). On top
    of that, the ``--glinet`` build gates the REST API behind its own
    auth : with ``users: []`` in config.yaml every ``/control/*`` call
    returns 403, so the controller's per-network DNS protection (which
    drives AdGuard persistent-clients) can't work.

    Two steps, both idempotent :
      1. Flip the UCI flag + start/enable the init.d service so :3000
         comes up (and survives reboot).
      2. ``bootstrap_admin()`` — inject a ``users:`` block (bcrypt of the
         router admin password) into config.yaml + restart, so the
         controller's HTTP Basic auth is accepted.

    Hardcoded prerequisite (no AdoptionOptions toggle) : DNS protection
    is a core feature and needs AdGuard reachable + authenticated.
    """
    ctx = _TaskCtx("Enable AdGuard Home")
    ctx.start()

    # 1. Enable + start the daemon. The enable triggers a fw3 reload
    #    (gl-sdk4-adguardhome installs firewall hooks) so allow 30s.
    enable_cmd = (
        "uci set adguardhome.config.enabled=1 && uci commit adguardhome && "
        "/etc/init.d/adguardhome enable 2>/dev/null; "
        "/etc/init.d/adguardhome start 2>/dev/null; echo OK"
    )
    try:
        r = await ssh.run(enable_cmd, timeout=45)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH enable failed: {exc}")
    if "OK" not in r.stdout:
        return ctx.failed(f"enable returned: {r.stdout!r} {r.stderr!r}")

    # 2. Provision the admin user so REST auth works. Build a throwaway
    #    AdGuardManager bound to this device's SSH + the router creds.
    from app.adguard.manager import AdGuardError, AdGuardManager
    # Include the Slate's tailscale IP as a fallback : AdGuard's :::3000
    # binding refuses LAN-IP connections coming via the tailnet tunnel
    # (see app/adguard/manager.py docstring). Discover the IP via SSH
    # at build time.
    adoption_adguard_hosts: list[str] = [ssh.host]
    try:
        tsip_r = await ssh.run(
            "tailscale ip -4 2>/dev/null | head -1", timeout=4,
        )
        ts_ip = tsip_r.stdout.strip()
        if ts_ip and ts_ip not in adoption_adguard_hosts:
            adoption_adguard_hosts.append(ts_ip)
    except Exception:  # noqa: BLE001
        pass
    mgr = AdGuardManager(
        ssh=ssh, slate_hosts=adoption_adguard_hosts,
        admin_username=settings.admin_username,
        admin_password=settings.admin_password,
    )
    try:
        # Give the daemon a moment to bind :3000 after start.
        provisioned = False
        for _ in range(20):
            if await mgr.is_admin_provisioned():
                provisioned = True
                break
            await asyncio.sleep(0.5)
        if provisioned:
            return ctx.ok("AdGuard enabled + already provisioned (:3000 OK)")
        await mgr.bootstrap_admin()
        return ctx.ok("AdGuard enabled + admin user provisioned (:3000 OK)")
    except AdGuardError as exc:
        return ctx.failed(f"AdGuard bootstrap failed: {exc}")
    finally:
        await mgr.aclose()


# Packages the controller features need on every adopted Slate. Listed
# here so the install happens at adoption time (one ``opkg update`` per
# pipeline, one shot for the whole batch) rather than lazily on first
# use of each feature (slow + needs WAN). Add to this when a new
# feature needs an opkg package.
#
# Current entries :
#   tcpdump     →  /api/network/pcap (Phase 1 LAN capture)
#
# Reserved for future phases (commented out to avoid forced installs
# until the feature lands) :
#   kmod-mt76x0u   →  Monitor mode Phase 2 (USB dongle ALFA AWUS036ACHM)
#   usbutils       →  ``lsusb`` for dongle detection in Phase 2
_EXTRA_PACKAGES: tuple[str, ...] = (
    "tcpdump",
)


async def _task_install_extra_packages(*, ssh: SlateSSH) -> AdoptionTaskReport:
    """Install controller-feature opkg packages in one shot.

    Skips packages already present (``opkg list-installed`` check), so
    re-adoption is cheap when nothing changed. A failed install on one
    package doesn't abort the rest — the task reports which succeeded
    and which didn't.
    """
    ctx = _TaskCtx(f"Install extra packages ({', '.join(_EXTRA_PACKAGES)})")
    ctx.start()
    # Build the install command : one opkg-list pass to filter the
    # already-installed entries, then a single update + install for
    # the rest. ``2>&1`` keeps stderr in the report on failure.
    pkgs_quoted = " ".join(_EXTRA_PACKAGES)
    cmd = (
        f"MISSING=''; "
        f"for p in {pkgs_quoted}; do "
        f"  opkg list-installed | grep -q \"^$p \" || MISSING=\"$MISSING $p\"; "
        f"done; "
        f"if [ -z \"$MISSING\" ]; then echo NOTHING_TO_DO; exit 0; fi; "
        f"echo \"MISSING:$MISSING\"; "
        f"opkg update >/dev/null 2>&1 || {{ echo OPKG_UPDATE_FAILED; exit 1; }}; "
        f"opkg install $MISSING 2>&1 | tail -20; "
        f"echo INSTALL_DONE"
    )
    try:
        result = await ssh.run(cmd, timeout=120)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH failed: {exc}")
    out = result.stdout
    if "NOTHING_TO_DO" in out:
        return ctx.ok(f"all extras already installed ({pkgs_quoted})")
    if "OPKG_UPDATE_FAILED" in out:
        return ctx.failed(
            "opkg update failed — Slate has no WAN uplink ? "
            f"(extras: {pkgs_quoted})",
        )
    if "INSTALL_DONE" not in out:
        return ctx.failed(f"opkg install incomplete : {out.strip()[-200:]}")
    # Verify each package landed.
    failed: list[str] = []
    for p in _EXTRA_PACKAGES:
        try:
            r = await ssh.run(
                f"opkg list-installed | grep -q \"^{p} \" "
                f"&& echo PRESENT || echo MISSING",
                timeout=10,
            )
            if "PRESENT" not in r.stdout:
                failed.append(p)
        except SlateSSHError:
            failed.append(p)
    if failed:
        return ctx.failed(
            f"missing after install : {', '.join(failed)}",
        )
    return ctx.ok(f"installed : {pkgs_quoted}")


async def _task_disable_upnp(*, ssh: SlateSSH) -> AdoptionTaskReport:
    ctx = _TaskCtx("Disable UPnP")
    ctx.start()
    cmd = (
        # If miniupnpd config doesn't exist, this is a no-op (still OK).
        "uci -q set upnpd.config.enabled='0'; "
        "uci -q commit upnpd; "
        "/etc/init.d/miniupnpd stop 2>/dev/null; "
        "/etc/init.d/miniupnpd disable 2>/dev/null; "
        "echo OK"
    )
    try:
        result = await ssh.run(cmd)
    except SlateSSHError as exc:
        return ctx.failed(f"SSH failed: {exc}")
    if "OK" not in result.stdout:
        return ctx.failed(f"unexpected output: {result.stdout!r}")
    return ctx.ok("upnpd disabled + stopped")


async def _task_enable_doh_blocklist(
    *, ssh: SlateSSH, settings: Settings,
) -> AdoptionTaskReport:
    """Enable the HaGeZi DoH/VPN/Proxy bypass feed in AdGuard.

    The hardening audit's "Blocklist anti-bypass DoH/VPN" check looks
    for an enabled AdGuard filter whose URL contains ``hagezi-doh-vpn``
    (or whose name contains "doh"). This task adds the feed via
    `manager.add_filter` if missing, or flips it on via
    `manager.set_filter_enabled` if already present-but-disabled.

    Idempotent — running it twice on a happy slate returns "ok" both
    times with a noop message the second time.
    """
    ctx = _TaskCtx("Enable HaGeZi DoH/VPN blocklist")
    ctx.start()
    from app.adguard.feeds import get_feed
    from app.adguard.manager import AdGuardError, AdGuardManager

    feed = get_feed("hagezi-doh-vpn")
    if feed is None:
        # Shouldn't happen — feeds.py owns this slug. Fail loud if it does.
        return ctx.failed("feed slug 'hagezi-doh-vpn' missing from catalog")

    filter_seed_hosts: list[str] = [ssh.host]
    try:
        tsip_r = await ssh.run(
            "tailscale ip -4 2>/dev/null | head -1", timeout=4,
        )
        ts_ip = tsip_r.stdout.strip()
        if ts_ip and ts_ip not in filter_seed_hosts:
            filter_seed_hosts.append(ts_ip)
    except Exception:  # noqa: BLE001
        pass
    mgr = AdGuardManager(
        ssh=ssh, slate_hosts=filter_seed_hosts,
        admin_username=settings.admin_username,
        admin_password=settings.admin_password,
    )
    try:
        filters = await mgr.list_filters()
        match = next((f for f in filters if f.url == feed.url), None)
        if match is None:
            await mgr.add_filter(url=feed.url, name=feed.name)
            return ctx.ok(f"feed added : {feed.name}")
        if not match.enabled:
            await mgr.set_filter_enabled(url=feed.url, enabled=True)
            return ctx.ok(f"feed re-enabled : {feed.name}")
        return ctx.ok(f"feed already active : {feed.name} (noop)")
    except AdGuardError as exc:
        return ctx.failed(f"AdGuard REST KO: {exc}")
    finally:
        await mgr.aclose()


async def run_adoption(
    *,
    device_slug: str,
    host: str,
    rpc_port: int,
    options: AdoptionOptions,
    ssh: SlateSSH,
    store: DeviceStore,
    keypair_store: SSHKeypairStore,
    settings: Settings,
    wifi_store: WifiSsidStore,
) -> AdoptionRunReport:
    """Run all selected hardening tasks. Always returns — never raises.

    Caller is responsible for instantiating a working `SlateSSH` against the
    target device (creds + host known).
    """
    reports: list[AdoptionTaskReport] = []

    # PREREQUISITE 0 : make sure the SSH channel actually works before
    # we hand it to other tasks. Self-heals if the device was factory-reset
    # (key gone from authorized_keys while DB still thinks it's deployed).
    # Hardcoded — every adoption needs this, no toggle.
    reports.append(await _task_ensure_ssh_access(
        ssh=ssh, keypair_store=keypair_store, device_slug=device_slug,
    ))
    if reports[-1].status == "failed":
        # Without a working channel, no other SSH-based task can succeed.
        # Short-circuit so we don't drown the report in identical
        # "Permission denied" errors on each subsequent task.
        statuses = {r.status for r in reports}
        overall = "failed" if "failed" in statuses else "ok"
        await store.mark_probed(device_slug, status="error")
        logger.warning(
            "devices.adoption.aborted_no_ssh",
            device=device_slug,
        )
        return AdoptionRunReport(
            device_slug=device_slug,
            overall_status=overall,
            tasks=reports,
        )

    if options.pin_tls:
        reports.append(await _task_pin_tls(
            device_slug=device_slug, host=host, port=rpc_port, store=store,
        ))
    if options.force_https_webui:
        reports.append(await _task_force_https(ssh=ssh))
    if options.ssh_key_only:
        reports.append(await _task_ssh_key_only(
            ssh=ssh, keypair_store=keypair_store, device_slug=device_slug,
        ))
    # LuCI access is a PREREQUISITE, not an option : every adoption
    # enables it unconditionally so the controller / operator has
    # advanced web UI access for debugging or operations the GL.iNet
    # UI doesn't expose. Hardcoded on purpose, no flag in AdoptionOptions.
    reports.append(await _task_enable_luci(ssh=ssh))
    # Install the opkg packages controller features depend on (tcpdump
    # for /api/network/pcap, and any future entries in _EXTRA_PACKAGES).
    # Runs alongside luci so a single opkg update covers both — and so
    # PCAP capture from the UI never has to wait for a first-use install.
    reports.append(await _task_install_extra_packages(ssh=ssh))
    # Same convention : locking the WAN admin surface (ping/HTTPS/SSH)
    # is a non-negotiable security baseline. Hardcoded.
    reports.append(await _task_lock_wan_admin(ssh=ssh))
    # Enable + provision AdGuard Home so per-network DNS protection works.
    # Hardcoded prerequisite (factory reset leaves it disabled + unauth'd).
    reports.append(await _task_enable_adguard(ssh=ssh, settings=settings))
    # Push the slate-ctrl agent + handlers + secrets — adoption means
    # "this Slate is now controller-managed", agent must follow.
    reports.append(await _task_deploy_agent(
        ssh=ssh, settings=settings, wifi_store=wifi_store,
    ))
    if options.disable_upnp:
        reports.append(await _task_disable_upnp(ssh=ssh))

    statuses = {r.status for r in reports}
    if statuses == {"ok"} or statuses <= {"ok", "skipped"}:
        overall = "ok"
    elif "ok" in statuses:
        overall = "partial"
    else:
        overall = "failed"

    if overall in ("ok", "partial"):
        await store.mark_adopted(device_slug)
    else:
        await store.mark_probed(device_slug, status="error")

    logger.info(
        "devices.adoption.complete",
        device=device_slug,
        overall=overall,
        tasks={r.name: r.status for r in reports},
    )

    return AdoptionRunReport(
        device_slug=device_slug,
        overall_status=overall,
        tasks=reports,
    )
