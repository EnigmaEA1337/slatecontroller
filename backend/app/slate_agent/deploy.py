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
REMOTE_ADGUARD_SECRET = f"{REMOTE_SECRETS_DIR}/adguard.env"

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
            f"{REMOTE_STATE_DIR} {REMOTE_SCREENS_DIR} /usr/local/bin && "
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

    logger.info(
        "slate_agent.deploy",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


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
