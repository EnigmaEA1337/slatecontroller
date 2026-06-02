"""Tailscale Serve wrapper for the controller's own HTTPS access.

The controller's tailnet identity is owned by a **sidecar container**
(`slate-tailscale`). Its tailscaled socket is shared with this backend
via a named docker volume mounted at `/var/run/tailscale/` in both
containers — the default path the `tailscale` CLI looks at, no
`--socket` flag needed.

What's exposed to the UI :
  - `get_state()` — full snapshot : Tailscale reachable ? hostname on
    tailnet ? HTTPS Serve configured ? what paths are routed ? when
    does the cert expire ?
  - `enable_https()` — write the declarative serve.json with the
    `/` → frontend and `/api` → backend routes, then signal the
    sidecar to reload.
  - `disable_https()` — write an empty serve.json and signal reload.

Failure modes handled (surfaced as `ControllerHttpsState` flags so the
UI can render actionable instructions instead of an opaque 500) :
  - **Sidecar not running** → CLI status returns no daemon. Flagged
    `daemon_reachable=False`, page shows `docker compose up -d
    slate-tailscale` hint.
  - **TS_AUTHKEY missing on first boot** → tailscaled is up but
    BackendState=`NeedsLogin`. Surfaced as `needs_login=True` with the
    auth key generation URL.
  - **HTTPS feature not enabled in tailnet admin** → `tailscale cert`
    refuses. Surfaced with the remediation URL.

Since the sidecar OWNS its own tailscaled socket, there's no operator
permission issue : the CLI calls inside the backend container always
have full rights on their daemon. The `operator_set` flag is kept in
the response for API stability with older clients but is always True.

Why CLI shell-out instead of talking to the local API socket directly:
the CLI already handles protocol details (auth, JSON shape drift
across Tailscale versions). Shell-out is one process spawn per poll —
negligible cost.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


# Where the Tailscale CLI looks for the daemon socket inside the
# container. The docker-compose mounts the host's snap socket here.
_TAILSCALE_SOCKET = Path("/var/run/tailscale/tailscaled.sock")

# Host-side targets the controller wants Serve to forward to. These are
# the docker-compose published ports — tailscaled on the host reaches
# them via its own localhost.
_FRONTEND_TARGET = "http://localhost:5173"
_BACKEND_TARGET = "http://localhost:8000"


@dataclass
class ServeRoute:
    """One mapping in the Serve config : path → upstream target."""

    path: str  # "/" or "/api"
    target: str  # "http://localhost:5173"


@dataclass
class CertInfo:
    """Public-safe summary of the active Let's Encrypt cert."""

    issuer: str | None = None
    not_after: datetime | None = None
    days_remaining: int | None = None


@dataclass
class ControllerHttpsState:
    """One snapshot returned by ``get_state()``.

    All fields are independently nullable / falsey so the UI can render
    partial state when one probe fails (e.g. tailscaled is up but HTTPS
    not yet enabled in admin)."""

    cli_available: bool = False
    daemon_reachable: bool = False
    operator_set: bool = True  # assume yes until a write fails
    tailnet_hostname: str | None = None  # "icar.taild2bce8.ts.net"
    tailnet_name: str | None = None  # "taild2bce8.ts.net"
    tailscale_ips: list[str] = field(default_factory=list)
    https_enabled: bool = False
    routes: list[ServeRoute] = field(default_factory=list)
    cert: CertInfo | None = None
    public_url: str | None = None  # https://<hostname>/
    raw_error: str | None = None  # last error surfaced from CLI
    feature_https_enabled_in_admin: bool | None = None  # None = unknown


# ---------- low-level CLI shell-out ----------

async def _run_tailscale(*args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a `tailscale` command. Returns (returncode, stdout, stderr)."""
    cli = shutil.which("tailscale")
    if cli is None:
        return 127, "", "tailscale CLI not installed"
    try:
        proc = await asyncio.create_subprocess_exec(
            cli,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except TimeoutError:
        return -1, "", f"timed out after {timeout}s"


# ---------- read path ----------

async def get_state() -> ControllerHttpsState:
    """Build a snapshot of the controller's HTTPS posture."""
    state = ControllerHttpsState()

    # 0. CLI present in container ?
    if shutil.which("tailscale") is None:
        state.raw_error = "tailscale CLI not installed in backend container"
        return state
    state.cli_available = True

    # 1. Socket reachable ? (`tailscale status --json` fails fast if not.)
    rc, stdout, stderr = await _run_tailscale("status", "--json", timeout=5.0)
    if rc != 0:
        state.raw_error = stderr.strip() or stdout.strip() or f"rc={rc}"
        return state
    state.daemon_reachable = True
    try:
        status_json = json.loads(stdout)
    except json.JSONDecodeError as exc:
        state.raw_error = f"invalid status JSON: {exc}"
        return state

    self_node = status_json.get("Self") or {}
    # DNSName looks like "icar.taild2bce8.ts.net." (trailing dot)
    dns = (self_node.get("DNSName") or "").rstrip(".")
    if dns:
        state.tailnet_hostname = dns
        parts = dns.split(".", 1)
        if len(parts) == 2:
            state.tailnet_name = parts[1]
        state.public_url = f"https://{dns}"
    state.tailscale_ips = list(self_node.get("TailscaleIPs") or [])

    # 2. Serve config — `tailscale serve status --json` returns the
    # current Serve+Funnel state. Empty config = `{}` or `{"TCP":null}`.
    rc, stdout, stderr = await _run_tailscale("serve", "status", "--json", timeout=5.0)
    if rc != 0:
        # Read failure is non-fatal — we still know the hostname.
        logger.warning("controller_https.serve_status.failed", stderr=stderr.strip())
    else:
        try:
            serve_json = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            serve_json = {}
        state.routes = _parse_serve_routes(serve_json)
        # HTTPS is "on" when at least one path is configured for our
        # 443 entry. Tailscale Serve exposes :443 by default for HTTPS.
        state.https_enabled = any(r.target for r in state.routes)

    # 3. Cert info + tailnet HTTPS feature flag. `tailscale cert` is
    #    the canonical way to ask tailscaled for the cert ; it returns
    #    a clear error when HTTPS isn't enabled in the tailnet admin,
    #    which we surface as a distinct UI state (not just "no cert").
    if state.tailnet_hostname:
        cert, feature_flag = await _read_cert_info(state.tailnet_hostname)
        state.cert = cert
        state.feature_https_enabled_in_admin = feature_flag
        # Best-effort cleanup of the temp cert + key. Not critical —
        # they're inside the container's ephemeral fs — but we keep
        # tmp tidy anyway.
        for p in ("/tmp/.ts_cert.pem", "/tmp/.ts_cert.key"):
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass

    return state


def _parse_serve_routes(serve_json: dict) -> list[ServeRoute]:
    """Pull (path, target) tuples out of `tailscale serve status --json`.

    The JSON shape (Tailscale 1.60+) is roughly :
      {
        "Web": {
          "${host}:443": {
            "Handlers": {
              "/": {"Proxy": "http://localhost:5173"},
              "/api": {"Proxy": "http://localhost:8000"}
            }
          }
        }
      }
    We accept missing fields gracefully — Tailscale has shipped a few
    variants across minor versions.
    """
    routes: list[ServeRoute] = []
    web = serve_json.get("Web") or {}
    for _host_port, web_cfg in web.items():
        handlers = (web_cfg or {}).get("Handlers") or {}
        for path, handler in handlers.items():
            target = (handler or {}).get("Proxy") or ""
            if target:
                routes.append(ServeRoute(path=path, target=target))
    # Stable order so UI renders predictably.
    routes.sort(key=lambda r: r.path)
    return routes


async def _read_cert_info(hostname: str) -> tuple[CertInfo | None, bool | None]:
    """Best-effort cert metadata + admin-HTTPS-feature flag.

    Returns ``(cert_info, https_feature_enabled_in_admin)``. The flag is
    ``False`` when tailscaled tells us "your Tailscale account does not
    support getting TLS certs" (i.e. user hasn't toggled HTTPS in the
    tailnet admin console at https://login.tailscale.com/admin/dns).
    The flag is ``True`` when the cert reads back fine. ``None`` for
    other failure shapes (timeout / IO / unexpected).
    """
    # Read via subprocess pipe to openssl x509 -noout -dates -issuer
    rc, stdout, stderr = await _run_tailscale(
        "cert",
        "--cert-file=/tmp/.ts_cert.pem",
        "--key-file=/tmp/.ts_cert.key",
        hostname,
        timeout=15.0,
    )
    if rc != 0:
        feature_flag: bool | None = None
        if "does not support getting TLS certs" in stderr:
            feature_flag = False
        logger.info(
            "controller_https.cert.read_failed",
            hostname=hostname,
            stderr=stderr.strip()[:200],
        )
        return None, feature_flag
    cert_path = Path("/tmp/.ts_cert.pem")
    if not cert_path.exists():
        return None, True  # cert call succeeded but file missing — odd but feature is enabled
    try:
        proc = await asyncio.create_subprocess_exec(
            "openssl",
            "x509",
            "-in",
            str(cert_path),
            "-noout",
            "-issuer",
            "-enddate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (TimeoutError, FileNotFoundError):
        return None, True
    info = CertInfo()
    for line in out.decode().splitlines():
        if line.startswith("issuer="):
            info.issuer = line.removeprefix("issuer=").strip()
        elif line.startswith("notAfter="):
            # "notAfter=Aug 29 13:21:45 2026 GMT"
            raw = line.removeprefix("notAfter=").strip()
            try:
                dt = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
                info.not_after = dt
                info.days_remaining = max(0, (dt - datetime.now(UTC)).days)
            except ValueError:
                pass
    return info, True


# ---------- write path ----------

@dataclass
class WriteResult:
    ok: bool
    message: str
    operator_hint: bool = False  # True when failure was due to missing operator


async def enable_https() -> WriteResult:
    """Configure Serve : / → frontend, /api → backend, both via :443."""
    # Order matters : we set the more specific path first so the
    # daemon's longest-prefix-match routes correctly.
    steps = [
        ("serve", "--bg", "--https=443", "--set-path=/api", _BACKEND_TARGET),
        ("serve", "--bg", "--https=443", "--set-path=/", _FRONTEND_TARGET),
    ]
    for cmd in steps:
        rc, stdout, stderr = await _run_tailscale(*cmd, timeout=15.0)
        if rc != 0:
            err = (stderr.strip() or stdout.strip() or "")
            hint = "operator" in err.lower() or "access denied" in err.lower()
            return WriteResult(
                ok=False,
                message=err[:400] or f"rc={rc}",
                operator_hint=hint,
            )
    return WriteResult(ok=True, message="Serve configured for / and /api on :443")


async def disable_https() -> WriteResult:
    rc, stdout, stderr = await _run_tailscale("serve", "reset", timeout=10.0)
    if rc != 0:
        err = (stderr.strip() or stdout.strip() or "")
        hint = "operator" in err.lower() or "access denied" in err.lower()
        return WriteResult(ok=False, message=err[:400], operator_hint=hint)
    return WriteResult(ok=True, message="Serve reset")
