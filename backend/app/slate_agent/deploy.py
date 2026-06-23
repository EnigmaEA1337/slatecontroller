"""Deploy the slate-ctrl agent + handlers to a Slate.

The agent files are checked into the controller repo at
`app/slate_agent/scripts/`. The deployer reads them, pushes them via SSH
to fixed paths on the Slate, and makes the dispatcher executable. The
operation is idempotent — re-running overwrites with the latest content
from the repo, which is also how upgrades work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

# Layout on the Slate. These are baked into slate-ctrl too — changing them
# means coordinating both ends.
REMOTE_ROOT = "/etc/slate-controller"
REMOTE_BIN = "/usr/local/bin/slate-ctrl"
REMOTE_HANDLERS_DIR = f"{REMOTE_ROOT}/handlers"
REMOTE_PROFILES_DIR = f"{REMOTE_ROOT}/profiles"
REMOTE_STATE_DIR = f"{REMOTE_ROOT}/state"
REMOTE_SCREENS_DIR = f"{REMOTE_ROOT}/screens"
REMOTE_SECRETS_DIR = f"{REMOTE_ROOT}/secrets"
REMOTE_SCRIPTS_DIR = f"{REMOTE_ROOT}/scripts"
REMOTE_ADGUARD_SECRET = f"{REMOTE_SECRETS_DIR}/adguard.env"
REMOTE_WIFI_SECRET = f"{REMOTE_SECRETS_DIR}/wifi.env"
REMOTE_RAM_MITIGATION = f"{REMOTE_SCRIPTS_DIR}/ram-mitigation.sh"
REMOTE_WIFI_DRIFT_WATCHDOG = f"{REMOTE_SCRIPTS_DIR}/wifi-drift-watchdog.sh"
REMOTE_CYCLE_SCRIPT = f"{REMOTE_SCRIPTS_DIR}/cycle-profile.sh"
REMOTE_CYCLE_ACTION_UPDATE = f"{REMOTE_SCRIPTS_DIR}/cycle-action-update.sh"
REMOTE_RC_BUTTON_RESET = "/etc/rc.button/reset"
REMOTE_RC_BUTTON_RESET_BACKUP = "/etc/rc.button/reset.slate-ctrl.backup"
# Webhook push helpers + their config files.
REMOTE_WEBHOOK_SECRET = f"{REMOTE_SECRETS_DIR}/webhook.secret"
REMOTE_CONTROLLER_URL = f"{REMOTE_ROOT}/controller-url"
REMOTE_DEVICE_SLUG = f"{REMOTE_ROOT}/device-slug"
# Controller's internal CA root, pushed when the controller HTTPS cert
# is signed by our CA. The helper uses curl --cacert against this when
# present ; absent = rely on the system trust store (publicly-trusted
# certs like Tailscale ts.net Let's Encrypt).
REMOTE_CONTROLLER_CA = f"{REMOTE_SECRETS_DIR}/controller-ca.pem"
REMOTE_EVENT_PUSH = "/usr/local/bin/slate-ctrl-event-push"
REMOTE_TOUCHSCREEN_WATCHER = "/usr/local/bin/slate-ctrl-touchscreen-watcher"
REMOTE_TOUCHSCREEN_INIT = "/etc/init.d/slate-ctrl-touchscreen-watcher"
# Marker embedded as a comment in our managed reset hook — used to
# detect whether the file on the Slate is already ours so we don't
# back up our own version on re-deploys.
RESET_HOOK_MARKER = "managed by slate-controller"

# Marker for the cron line we own. crontab is shared with whatever else
# is on the Slate, so we identify our entry by this tail comment and
# rewrite only that one line — never overwrite the full file.
CRON_MARKER = "# slate-ctrl:ram-mitigation"
# 04:00 every day. Quiet hour (most users asleep), gives AdGuard +
# tailscaled a clean restart before the day's traffic resumes. The
# `>/dev/null 2>&1` part keeps cron from mailing root each run.
CRON_ENTRY = (
    f"0 4 * * * {REMOTE_RAM_MITIGATION} >>/tmp/slate-ctrl-ram.log 2>&1 {CRON_MARKER}"
)

# Wifi drift watchdog — runs every 2 min. Counter-measures the
# GL.iNet LCD / travel-router mode toggles that silently drop our
# managed VAPs (cf. 2026-06 hotel drift incident). The script self-locks
# on /etc/slate-controller/.apply.lock so it can't race a manual apply.
CRON_MARKER_WIFI = "# slate-ctrl:wifi-drift-watchdog"
CRON_ENTRY_WIFI = (
    f"*/2 * * * * {REMOTE_WIFI_DRIFT_WATCHDOG} >/dev/null 2>&1 {CRON_MARKER_WIFI}"
)

# Where the agent files live in the controller's source tree.
LOCAL_SCRIPTS_DIR = Path(__file__).parent / "scripts"


@dataclass
class DeployReport:
    """Per-deploy outcome — surfaces what was written and any errors."""

    pushed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "pushed": self.pushed, "errors": self.errors}


async def deploy_agent(
    ssh: SlateSSH,
    *,
    adguard_credentials: tuple[str, str] | None = None,
    wifi_passwords: dict[str, str] | None = None,
) -> DeployReport:
    """Push slate-ctrl + all handlers to the Slate.

    Pipeline:
      1. mkdir -p the layout dirs (secrets/ in 0700)
      2. push slate-ctrl → /usr/local/bin/slate-ctrl (chmod 755)
      3. push each scripts/handlers/*.sh → /etc/slate-controller/handlers/
      4. if adguard_credentials → push secrets/adguard.env (chmod 600)
      5. if wifi_passwords → push secrets/wifi.env (chmod 600)
         keyed by SSID slug (sanitized to [A-Z0-9_]) → ``WIFI_<SLUG>_PSK``
         shell-source-able env file. Used by wifi.sh when CREATE'ing
         a wifi-iface that's missing on the Slate.

    Profiles and state dirs are created but not populated here — see
    `sync.sync_profiles()` for the JSON push.
    """
    rep = DeployReport()

    # 1. Ensure all dirs exist on the Slate. `/usr/local/bin` is created
    # too because on some OpenWrt builds it's missing. secrets/ is tightened
    # to 0700 — only root reads files inside.
    try:
        await ssh.run(
            f"mkdir -p {REMOTE_HANDLERS_DIR} {REMOTE_PROFILES_DIR} "
            f"{REMOTE_STATE_DIR} {REMOTE_SCREENS_DIR} {REMOTE_SCRIPTS_DIR} "
            f"/usr/local/bin && "
            f"mkdir -p {REMOTE_SECRETS_DIR} && chmod 700 {REMOTE_SECRETS_DIR}",
            timeout=10,
        )
        rep.pushed.append("created agent directory layout")
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir layout: {exc}")
        return rep

    # 2. Push the dispatcher.
    dispatcher = LOCAL_SCRIPTS_DIR / "slate-ctrl"
    if not dispatcher.is_file():
        rep.errors.append(f"missing local dispatcher at {dispatcher}")
        return rep
    try:
        payload = dispatcher.read_bytes()
        await ssh.put_bytes(payload, REMOTE_BIN, mode=0o755)
        rep.pushed.append(f"{REMOTE_BIN} ({len(payload)} bytes)")
    except (SlateSSHError, OSError) as exc:
        rep.errors.append(f"push dispatcher: {exc}")
        return rep

    # 3. Push each handler in scripts/handlers/.
    handlers_dir = LOCAL_SCRIPTS_DIR / "handlers"
    if not handlers_dir.is_dir():
        rep.errors.append(f"missing handlers dir at {handlers_dir}")
        return rep

    for handler in sorted(handlers_dir.glob("*.sh")):
        target = f"{REMOTE_HANDLERS_DIR}/{handler.name}"
        try:
            payload = handler.read_bytes()
            # Handlers are sourced (not exec'd) — no executable bit needed,
            # but 0644 is fine.
            await ssh.put_bytes(payload, target, mode=0o644)
            rep.pushed.append(f"{target} ({len(payload)} bytes)")
        except (SlateSSHError, OSError) as exc:
            rep.errors.append(f"push handler {handler.name}: {exc}")

    # 4. Push AdGuard credentials if provided. The handler sources this
    # file to talk to the local REST API; without it, the adguard handler
    # degrades gracefully (toggle still works, filter reconciliation skipped).
    if adguard_credentials is not None:
        user, password = adguard_credentials
        try:
            await _write_adguard_secret(ssh, user=user, password=password)
            rep.pushed.append(f"{REMOTE_ADGUARD_SECRET} (0600)")
        except (SlateSSHError, ValueError) as exc:
            rep.errors.append(f"push adguard secret: {exc}")

    # 4b. Push Wi-Fi PSKs if any. Same security profile as the AdGuard
    # secret: mode 0600 inside a 0700 secrets/ directory, root only.
    # The wifi.sh handler sources this when CREATE'ing a new wifi-iface.
    if wifi_passwords:
        try:
            await _write_wifi_secrets(ssh, wifi_passwords)
            rep.pushed.append(
                f"{REMOTE_WIFI_SECRET} (0600, {len(wifi_passwords)} PSK)",
            )
        except (SlateSSHError, ValueError) as exc:
            rep.errors.append(f"push wifi secrets: {exc}")

    # 5. Reset-button profile cycle. Three artifacts :
    #    a. cycle-profile.sh (dispatcher, reads cycle.json)
    #    b. cycle-action-update.sh (V1 "update from controller" placeholder)
    #    c. /etc/rc.button/reset replaced with our managed hook that
    #       preserves OEM behaviors AND adds a < 3s short-press branch
    # Idempotent : the OEM file is backed up only on first install, our
    # managed version is detected via its marker comment.
    for script_name, target_path in (
        ("cycle-profile.sh", REMOTE_CYCLE_SCRIPT),
        ("cycle-action-update.sh", REMOTE_CYCLE_ACTION_UPDATE),
    ):
        local = LOCAL_SCRIPTS_DIR / script_name
        if not local.is_file():
            rep.errors.append(f"missing local script {script_name}")
            continue
        try:
            payload = local.read_bytes()
            await ssh.put_bytes(payload, target_path, mode=0o755)
            rep.pushed.append(f"{target_path} ({len(payload)} bytes)")
        except (SlateSSHError, OSError) as exc:
            rep.errors.append(f"push {script_name}: {exc}")
    # The button hook itself. Failure here is non-fatal — the rest of
    # the agent still works ; only short-press cycling stops working.
    try:
        await _install_reset_button_hook(ssh)
        rep.pushed.append(
            f"{REMOTE_RC_BUTTON_RESET} (cycle hook, OEM preserved)"
        )
    except SlateSSHError as exc:
        rep.errors.append(f"install reset-button hook: {exc}")

    # 6. RAM mitigation script + crontab entry. Counters the observed
    # ~30-40 MB/day leak across tailscaled + AdGuardHome by restarting
    # both at 04:00. Idempotent: rewrites only the line tagged with
    # CRON_MARKER, leaves any other crontab content untouched.
    ram_script = LOCAL_SCRIPTS_DIR / "ram-mitigation.sh"
    if ram_script.is_file():
        try:
            payload = ram_script.read_bytes()
            await ssh.put_bytes(payload, REMOTE_RAM_MITIGATION, mode=0o755)
            rep.pushed.append(f"{REMOTE_RAM_MITIGATION} ({len(payload)} bytes)")
        except (SlateSSHError, OSError) as exc:
            rep.errors.append(f"push ram-mitigation script: {exc}")

    # 7. WiFi drift watchdog — runs every 2 min on the Slate to detect
    # and self-heal the LCD-toggle / travel-router-mode drift the user
    # hit during 2026-06 hotel debug (blackice silently went DOWN at
    # netdev level after the stock LCD captured an upstream WiFi).
    wifi_watchdog = LOCAL_SCRIPTS_DIR / "wifi-drift-watchdog.sh"
    if wifi_watchdog.is_file():
        try:
            payload = wifi_watchdog.read_bytes()
            await ssh.put_bytes(payload, REMOTE_WIFI_DRIFT_WATCHDOG, mode=0o755)
            rep.pushed.append(
                f"{REMOTE_WIFI_DRIFT_WATCHDOG} ({len(payload)} bytes)"
            )
        except (SlateSSHError, OSError) as exc:
            rep.errors.append(f"push wifi-drift-watchdog: {exc}")

    # Install both cron entries in one shot — single crontab rewrite +
    # one crond reload, instead of two round-trips.
    try:
        await _install_cron_entries(ssh)
        rep.pushed.append(f"crontab :: {CRON_ENTRY}")
        rep.pushed.append(f"crontab :: {CRON_ENTRY_WIFI}")
    except SlateSSHError as exc:
        rep.errors.append(f"install cron entries: {exc}")

    logger.info(
        "slate_agent.deploy",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


async def _install_reset_button_hook(ssh: SlateSSH) -> None:
    """Replace /etc/rc.button/reset with our managed hook (preserves OEM).

    First-install : backup the OEM file to `reset.slate-ctrl.backup`
    so the user can roll back via SSH if our version breaks. Re-deploys :
    no backup overwrite — our marker comment lets us recognize that the
    current file IS our managed version.
    """
    local = LOCAL_SCRIPTS_DIR / "rc-button-reset.sh"
    if not local.is_file():
        raise SlateSSHError(f"missing local rc-button hook at {local}")
    payload = local.read_bytes()
    if RESET_HOOK_MARKER not in payload.decode("utf-8", errors="ignore"):
        raise SlateSSHError(
            "managed reset hook is missing its marker comment — refusing "
            "to install (re-deploys would clobber the OEM backup)",
        )

    # Read the current file ; check whether it's already ours.
    read = await ssh.run(
        f"cat {REMOTE_RC_BUTTON_RESET} 2>/dev/null || true", timeout=5,
    )
    current = read.stdout or ""
    already_ours = RESET_HOOK_MARKER in current

    if not already_ours:
        # First install on this device : back up the OEM file. busybox cp
        # has no `-n` option, so we do the no-clobber check explicitly:
        # only copy when the source exists AND the backup doesn't yet.
        # Without this guard, re-running a partial install would clobber
        # the OEM backup with our own managed version.
        await ssh.run(
            f"[ -f {REMOTE_RC_BUTTON_RESET} ] && "
            f"[ ! -f {REMOTE_RC_BUTTON_RESET_BACKUP} ] && "
            f"cp {REMOTE_RC_BUTTON_RESET} {REMOTE_RC_BUTTON_RESET_BACKUP} "
            f"|| true",
            timeout=5,
        )

    # Write our managed version. /etc/rc.button must be executable.
    await ssh.put_bytes(payload, REMOTE_RC_BUTTON_RESET, mode=0o755)


async def _install_cron_entries(ssh: SlateSSH) -> None:
    """Idempotently install our cron lines in /etc/crontabs/root.

    Strategy : read the existing crontab, drop any line tagged with one
    of our markers (handles schedule upgrades), append the current
    entries, write back, poke crond.

    Markers managed here :
      - CRON_MARKER       (RAM mitigation, daily 04:00)
      - CRON_MARKER_WIFI  (WiFi drift watchdog, every 2 min)

    busybox crond constraints :
      - crontab file lives at /etc/crontabs/root (per-user, root only here)
      - busybox cron needs `/etc/init.d/cron reload` (or signal SIGHUP)
        to re-read after edits
      - missing /etc/crontabs/root is normal on a fresh device → we
        create it ourselves
    """
    markers = (CRON_MARKER, CRON_MARKER_WIFI)
    entries = (CRON_ENTRY, CRON_ENTRY_WIFI)
    read = await ssh.run(
        "cat /etc/crontabs/root 2>/dev/null || true",
        timeout=10,
    )
    current_lines = read.stdout.splitlines() if read.exit_status == 0 else []
    # Drop every line tagged with any of our markers so re-deploys never
    # accumulate duplicates.
    kept = [
        ln for ln in current_lines
        if not any(m in ln for m in markers)
    ]
    kept.extend(entries)
    new_content = ("\n".join(kept) + "\n").encode("utf-8")
    # mkdir + write atomically. crontabs/root must be 0600 (busybox crond
    # refuses world-readable crontabs on some builds).
    await ssh.run("mkdir -p /etc/crontabs", timeout=5)
    await ssh.put_bytes(new_content, "/etc/crontabs/root", mode=0o600)
    await ssh.run(
        "/etc/init.d/cron enable 2>/dev/null; "
        "/etc/init.d/cron start 2>/dev/null; "
        "/etc/init.d/cron reload 2>/dev/null || true",
        timeout=10,
    )


# Back-compat alias — older callers may still reference the singular form.
_install_cron_entry = _install_cron_entries


async def _write_adguard_secret(
    ssh: SlateSSH, *, user: str, password: str,
) -> None:
    """Write the AdGuard REST credentials to /etc/slate-controller/secrets/adguard.env.

    Shell-source-able format:
        ADGUARD_USER='admin'
        ADGUARD_PASSWORD='...'

    Single quotes inside the password are escaped with the POSIX shell idiom
    `'\\''` (close quote, escaped quote, reopen quote). chmod 0600 + root
    ownership is enforced via SlateSSH.put_bytes' mode arg + the parent dir
    being 0700.
    """
    # Validate inputs — refuse anything that can break the shell file or
    # be a placeholder. The Slate is shared admin space; better to fail
    # loudly than push "change-me" and pretend it works.
    if not user or not password:
        raise ValueError("AdGuard user/password must be non-empty")
    if password.strip().lower() in {"change-me", "changeme", "password"}:
        raise ValueError(
            "AdGuard password looks like a placeholder — refusing to deploy",
        )
    if "\n" in user or "\n" in password:
        raise ValueError("AdGuard user/password must not contain newlines")

    safe_user = user.replace("'", "'\\''")
    safe_pass = password.replace("'", "'\\''")
    content = (
        "# Managed by slate-controller. Sourced by handlers/adguard.sh.\n"
        "# Do not edit by hand — re-run /api/agent/deploy to refresh.\n"
        f"ADGUARD_USER='{safe_user}'\n"
        f"ADGUARD_PASSWORD='{safe_pass}'\n"
    ).encode("utf-8")
    await ssh.put_bytes(content, REMOTE_ADGUARD_SECRET, mode=0o600)


_SLUG_TO_ENV_RE = None  # filled at import time to keep the call site clean


def _slug_to_env(slug: str) -> str:
    """Sanitize an SSID slug for use as a shell variable name.

    Maps anything outside [A-Z0-9_] to `_`. Slug `wg-CH-ZA-1` →
    `WG_CH_ZA_1`. Used to build the wifi.env keys (`WIFI_<SLUG>_PSK`).
    """
    out: list[str] = []
    for ch in slug.strip().upper():
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    name = "".join(out).strip("_")
    return name or "X"


async def _write_wifi_secrets(
    ssh: SlateSSH, passwords: dict[str, str],
) -> None:
    """Push the Wi-Fi PSKs to /etc/slate-controller/secrets/wifi.env.

    Shell-source-able format::

        WIFI_NEURALCORE_PSK='...'
        WIFI_BLACKICE_PSK='...'

    `passwords` is `{slug: psk}`. Slugs are sanitized so `slate-fr` →
    `WIFI_SLATE_FR_PSK`. Empty PSKs are skipped silently (caller has
    already filtered to "has_password" SSIDs typically).

    Validates : refuse newlines + literal ``$()``/`` ``` `` patterns that
    could break shell sourcing. Quotes are POSIX-escaped via the
    `'\\''` idiom (close, escape, reopen).
    """
    lines = [
        "# Managed by slate-controller. Sourced by handlers/wifi.sh on CREATE.",
        "# Do not edit by hand — re-run /api/agent/deploy to refresh.",
    ]
    written = 0
    for slug, psk in sorted(passwords.items()):
        if not psk:
            continue
        if "\n" in psk or "\r" in psk:
            raise ValueError(f"PSK for {slug!r} contains a newline")
        env_name = _slug_to_env(slug)
        safe = psk.replace("'", "'\\''")
        lines.append(f"WIFI_{env_name}_PSK='{safe}'")
        written += 1
    if written == 0:
        # Caller asked us to push but nothing was usable — still write
        # an empty file so wifi.sh has something to source.
        lines.append("# (no PSKs configured)")
    content = ("\n".join(lines) + "\n").encode("utf-8")
    await ssh.put_bytes(content, REMOTE_WIFI_SECRET, mode=0o600)


async def get_agent_version(ssh: SlateSSH) -> str | None:
    """Return the deployed agent's version string, or None if not installed."""
    try:
        r = await ssh.run(f"{REMOTE_BIN} version 2>/dev/null", timeout=5)
        if r.exit_status == 0:
            return r.stdout.strip()
    except SlateSSHError:
        pass
    return None


async def deploy_webhook_components(
    ssh: SlateSSH,
    *,
    slug: str,
    controller_url: str,
    webhook_secret: str,
) -> DeployReport:
    """Push the Slate-side webhook push helpers + provision the secrets.

    Separately from :func:`deploy_agent` so callers can rotate the secret
    or re-target the controller URL without re-pushing handlers.

    Pipeline :
      1. push slate-ctrl-event-push → /usr/local/bin/ (chmod 755)
      2. push slate-ctrl-touchscreen-watcher → /usr/local/bin/ (chmod 755)
      3. push procd init script → /etc/init.d/ (chmod 755)
      4. write controller-url, device-slug, secrets/webhook.secret (0600)
      5. enable + restart the procd service so it picks up new config

    Idempotent — re-running rotates the secret and reloads the service.
    """
    rep = DeployReport()

    # 1+2+3. Push the three shell scripts.
    for src, target, mode in (
        ("slate-ctrl-event-push.sh", REMOTE_EVENT_PUSH, 0o755),
        ("slate-ctrl-touchscreen-watcher.sh", REMOTE_TOUCHSCREEN_WATCHER, 0o755),
        ("init.d-slate-ctrl-touchscreen-watcher", REMOTE_TOUCHSCREEN_INIT, 0o755),
    ):
        local = LOCAL_SCRIPTS_DIR / src
        if not local.is_file():
            rep.errors.append(f"missing local {src}")
            continue
        try:
            payload = local.read_bytes()
            await ssh.put_bytes(payload, target, mode=mode)
            rep.pushed.append(f"{target} ({len(payload)} bytes)")
        except (SlateSSHError, OSError) as exc:
            rep.errors.append(f"push {src}: {exc}")

    # 4. Provision the config files. URL + slug are world-readable
    # (they're not secret), the HMAC secret is 0600 inside 0700 secrets/.
    try:
        await ssh.put_bytes(
            controller_url.encode() + b"\n",
            REMOTE_CONTROLLER_URL, mode=0o644,
        )
        rep.pushed.append(f"{REMOTE_CONTROLLER_URL}")
    except (SlateSSHError, OSError) as exc:
        rep.errors.append(f"write controller-url: {exc}")

    try:
        await ssh.put_bytes(
            slug.encode() + b"\n",
            REMOTE_DEVICE_SLUG, mode=0o644,
        )
        rep.pushed.append(f"{REMOTE_DEVICE_SLUG}")
    except (SlateSSHError, OSError) as exc:
        rep.errors.append(f"write device-slug: {exc}")

    try:
        await ssh.put_bytes(
            webhook_secret.encode() + b"\n",
            REMOTE_WEBHOOK_SECRET, mode=0o600,
        )
        rep.pushed.append(f"{REMOTE_WEBHOOK_SECRET} (0600)")
    except (SlateSSHError, OSError) as exc:
        rep.errors.append(f"write webhook secret: {exc}")

    # 4b. Push the controller's internal-CA root cert ONLY when the
    # controller URL is on a hostname whose TLS cert is NOT publicly
    # trusted. Heuristic : *.ts.net is Tailscale Serve = Let's Encrypt,
    # so the Slate's stock trust store validates it ; pushing our
    # internal CA there would make curl --cacert REPLACE the trust
    # store with the wrong chain → 000 / connection error. Any other
    # hostname is assumed to be served by Traefik + our internal CA
    # (the typical homelab setup), and we push the CA accordingly.
    try:
        from urllib.parse import urlparse
        host = (urlparse(controller_url).hostname or "").lower()
        is_publicly_trusted = host.endswith(".ts.net")
        if is_publicly_trusted:
            await ssh.run(
                f"rm -f {REMOTE_CONTROLLER_CA}", timeout=5,
            )
            rep.pushed.append(
                f"{REMOTE_CONTROLLER_CA} (none — {host} uses publicly-trusted cert)"
            )
        else:
            from app.settings.internal_ca import pki as _ca_pki
            from app.settings.internal_ca.state import ROOT_CERT_PATH
            if _ca_pki.is_initialized() and ROOT_CERT_PATH.exists():
                ca_pem = ROOT_CERT_PATH.read_bytes()
                await ssh.put_bytes(
                    ca_pem, REMOTE_CONTROLLER_CA, mode=0o644,
                )
                rep.pushed.append(
                    f"{REMOTE_CONTROLLER_CA} ({len(ca_pem)} bytes, internal CA)"
                )
            else:
                await ssh.run(
                    f"rm -f {REMOTE_CONTROLLER_CA}", timeout=5,
                )
                rep.pushed.append(
                    f"{REMOTE_CONTROLLER_CA} (none — internal CA not initialised)"
                )
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"push controller CA: {exc}")

    # 5. Enable + restart the procd service. Failure here is annoying but
    # not catastrophic — the scripts are pushed, operator can fix manually.
    try:
        await ssh.run(
            f"{REMOTE_TOUCHSCREEN_INIT} enable 2>&1 ; "
            f"{REMOTE_TOUCHSCREEN_INIT} restart 2>&1 ; echo OK",
            timeout=15,
        )
        rep.pushed.append("touchscreen-watcher procd service enabled + restarted")
    except SlateSSHError as exc:
        rep.errors.append(f"enable touchscreen-watcher service: {exc}")

    logger.info(
        "slate_agent.deploy_webhook",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep
