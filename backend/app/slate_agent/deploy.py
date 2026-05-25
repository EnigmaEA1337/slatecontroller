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
REMOTE_RAM_MITIGATION = f"{REMOTE_SCRIPTS_DIR}/ram-mitigation.sh"
REMOTE_CYCLE_SCRIPT = f"{REMOTE_SCRIPTS_DIR}/cycle-profile.sh"
REMOTE_CYCLE_ACTION_UPDATE = f"{REMOTE_SCRIPTS_DIR}/cycle-action-update.sh"
REMOTE_RC_BUTTON_RESET = "/etc/rc.button/reset"
REMOTE_RC_BUTTON_RESET_BACKUP = "/etc/rc.button/reset.slate-ctrl.backup"
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
) -> DeployReport:
    """Push slate-ctrl + all handlers to the Slate.

    Pipeline:
      1. mkdir -p the layout dirs (secrets/ in 0700)
      2. push slate-ctrl → /usr/local/bin/slate-ctrl (chmod 755)
      3. push each scripts/handlers/*.sh → /etc/slate-controller/handlers/
      4. if adguard_credentials → push secrets/adguard.env (chmod 600)

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
        else:
            try:
                await _install_cron_entry(ssh)
                rep.pushed.append(f"crontab :: {CRON_ENTRY}")
            except SlateSSHError as exc:
                rep.errors.append(f"install cron entry: {exc}")

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


async def _install_cron_entry(ssh: SlateSSH) -> None:
    """Idempotently install our cron line in /etc/crontabs/root.

    Strategy : read the existing crontab, drop any line tagged with
    CRON_MARKER (handles upgrades when we change the schedule), append
    our current line, write it back, then poke crond so it picks up the
    change without a full restart.

    busybox crond constraints :
      - crontab file lives at /etc/crontabs/root (per-user, root only here)
      - busybox cron needs `/etc/init.d/cron reload` (or signal SIGHUP)
        to re-read after edits
      - missing /etc/crontabs/root is normal on a fresh device → we
        create it ourselves
    """
    # Read whatever is there now ; tolerate "no such file".
    read = await ssh.run(
        "cat /etc/crontabs/root 2>/dev/null || true",
        timeout=10,
    )
    current_lines = read.stdout.splitlines() if read.exit_status == 0 else []
    # Drop our previous line(s). Use a substring match on the marker so
    # we don't depend on the exact CRON_ENTRY string staying stable.
    kept = [ln for ln in current_lines if CRON_MARKER not in ln]
    kept.append(CRON_ENTRY)
    new_content = ("\n".join(kept) + "\n").encode("utf-8")
    # mkdir + write atomically. crontabs/root must be 0600 (busybox crond
    # refuses to read world-readable crontabs on some builds).
    await ssh.run("mkdir -p /etc/crontabs", timeout=5)
    await ssh.put_bytes(new_content, "/etc/crontabs/root", mode=0o600)
    # Ensure crond is enabled + running, then poke it. enable+start are
    # idempotent ; reload prompts re-read of the file.
    await ssh.run(
        "/etc/init.d/cron enable 2>/dev/null; "
        "/etc/init.d/cron start 2>/dev/null; "
        "/etc/init.d/cron reload 2>/dev/null || true",
        timeout=10,
    )


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


async def get_agent_version(ssh: SlateSSH) -> str | None:
    """Return the deployed agent's version string, or None if not installed."""
    try:
        r = await ssh.run(f"{REMOTE_BIN} version 2>/dev/null", timeout=5)
        if r.exit_status == 0:
            return r.stdout.strip()
    except SlateSSHError:
        pass
    return None
