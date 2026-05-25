"""Adoption orchestrator — the 4 hardening tasks run after device add.

Each task is independent and reports a status. A task failing doesn't block
the others (we report `partial` at the end). The orchestrator is synchronous
(< 30s typical); we don't need a background queue for now.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.devices.models import AdoptionOptions, AdoptionRunReport, AdoptionTaskReport
from app.devices.store import DeviceStore
from app.devices.tls import fetch_cert
from app.settings.ssh_keys import SSHKeypairStore
from app.slate.ssh import SlateSSH, SlateSSHError

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


async def _task_force_https(*, ssh: SlateSSH) -> AdoptionTaskReport:
    """Set the GL.iNet `glconfig.general.webui_redirect` flag, force LuCI HTTP→HTTPS."""
    ctx = _TaskCtx("Force HTTPS web UI")
    ctx.start()
    # `webui_redirect=on` is the GL.iNet way to force HTTPS. The nginx
    # rewrite in uhttpd/luci will then 301 :80 → :443. We also flip
    # `uhttpd.main.redirect_https=1` as a belt-and-braces.
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
    return ctx.ok("uhttpd reloaded with redirect_https=1")


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


async def run_adoption(
    *,
    device_slug: str,
    host: str,
    rpc_port: int,
    options: AdoptionOptions,
    ssh: SlateSSH,
    store: DeviceStore,
    keypair_store: SSHKeypairStore,
) -> AdoptionRunReport:
    """Run all selected hardening tasks. Always returns — never raises.

    Caller is responsible for instantiating a working `SlateSSH` against the
    target device (creds + host known).
    """
    reports: list[AdoptionTaskReport] = []

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
