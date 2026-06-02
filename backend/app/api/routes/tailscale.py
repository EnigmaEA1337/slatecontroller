"""Tailscale management endpoints.

Flow:
  1. User pastes a Tailscale auth key (one-shot reusable+tagged from admin.tailscale.com)
     and the connection options (accept/advertise routes, exit-node).
  2. We persist the key encrypted in app_secrets and run `tailscale up` via SSH.
  3. Status endpoint reads `tailscale status --json` and returns a clean payload.

The auth key is never returned in any response — only a "configured" boolean.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import get_slate_ssh
from app.auth import User, get_current_user
from app.db.database import make_session_factory
from app.slate.ssh import SlateSSH
from app.tailscale.admin_api import TailscaleAdminAPI, TailscaleAdminAPIError
from app.tailscale.admin_store import TailscaleAdminStore
from app.tailscale.audit import TailscaleAuditor
from app.tailscale.client import TailscaleClient
from app.tailscale.ha_store import (
    FAILSAFE_MODES,
    MAX_CHECK_INTERVAL,
    MIN_CHECK_INTERVAL,
    TailscaleHAStore,
)
from app.tailscale.models import (
    TailscaleConfigInput,
    TailscaleConnectResponse,
    TailscaleStatus,
)
from app.tailscale.store import TailscaleStore

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tailscale", tags=["tailscale"])


def _store(request: Request) -> TailscaleStore:
    sf = make_session_factory(request.app.state.db_engine)
    return TailscaleStore(sf)


def _admin_store(request: Request) -> TailscaleAdminStore:
    sf = make_session_factory(request.app.state.db_engine)
    return TailscaleAdminStore(sf)


def _ha_store(request: Request) -> TailscaleHAStore:
    # Reuse the singleton installed in lifespan — the watchdog and the
    # API share the same instance so writes are observed within one tick.
    return request.app.state.tailscale_ha_store


@router.get("/status", response_model=TailscaleStatus)
async def get_status(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> TailscaleStatus:
    """Live status from the Slate (tailscale status --json).

    Augments the daemon-reported status with the user-intent fields from our
    last-applied config (accept_routes, advertised_routes target list, etc.)
    — those aren't directly exposed in `tailscale status --json`.
    """
    state = await TailscaleClient(ssh).get_status()
    # Overlay user-intent from the stored config so the UI doesn't lie
    # ("Routes acceptées: non" while we actually passed --accept-routes).
    try:
        cfg = await _store(request).get_metadata()
        applied = (cfg or {}).get("config") or {}
        if applied:
            state.accept_routes = bool(applied.get("accept_routes"))
            # advertised_routes from daemon shows APPROVED routes only.
            # If empty but we asked to advertise some, keep showing what we asked.
            asked = applied.get("advertise_routes") or []
            if not state.advertised_routes and asked:
                state.advertised_routes = list(asked)
            state.exit_node_enabled = bool(applied.get("advertise_exit_node"))
            if not state.use_exit_node and applied.get("exit_node"):
                state.use_exit_node = str(applied.get("exit_node"))
    except Exception:  # noqa: BLE001
        pass  # status without overlay is still useful
    return state


@router.get("/config")
async def get_config(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Last-applied config + has-auth-key flag. No secrets returned."""
    return await _store(request).get_metadata()


@router.post("/connect", response_model=TailscaleConnectResponse)
async def connect(
    body: TailscaleConfigInput,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> TailscaleConnectResponse:
    """Run `tailscale up` with the supplied (or stored) auth key + options.

    If `auth_key` is omitted in the body, we reuse the one previously stored.
    Returns the resulting status + an `auth_url` if Tailscale prompted for
    browser-based login (happens when the key is invalid/expired and no
    other auth is set).
    """
    store = _store(request)
    auth_key = body.auth_key
    if not auth_key:
        auth_key = await store.get_auth_key()
    if not auth_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="auth_key required (none stored yet)",
        )

    cfg = body.model_copy(update={"auth_key": auth_key})
    client = TailscaleClient(ssh)
    success, note, auth_url = await client.connect(cfg)

    # Persist config + key only if at least the daemon started; we accept a
    # browser-auth required state as "partially configured" too.
    cfg_to_store = body.model_dump(exclude={"auth_key"})
    await store.save(
        auth_key=body.auth_key,  # only store new key if user provided one
        last_applied_config=cfg_to_store,
    )
    logger.info(
        "tailscale.connect",
        username=user.username,
        success=success,
        has_auth_url=bool(auth_url),
        advertise_routes=cfg.advertise_routes,
        advertise_exit_node=cfg.advertise_exit_node,
        exit_node=cfg.exit_node,
    )

    return TailscaleConnectResponse(
        success=success,
        status=await client.get_status(),
        note=note,
        auth_url=auth_url,
    )


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """`tailscale down` — keep daemon running, just leave the tailnet."""
    ok, note = await TailscaleClient(ssh).disconnect()
    logger.info("tailscale.disconnect", username=user.username, ok=ok, note=note)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Wipe device identity on the Slate AND clear stored auth key."""
    ok, note = await TailscaleClient(ssh).logout()
    await _store(request).clear()
    logger.warning(
        "tailscale.logout", username=user.username, ok=ok, note=note
    )


@router.get("/audit")
async def audit(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Combined Tailscale security audit (local + cloud if PAT configured).

    Returns a score 0-100, grade A-F, and a list of findings with
    severity, evidence, and remediation. Cloud checks (ACL, key hygiene,
    device approval, etc.) are added automatically when a PAT is saved
    via POST /api/tailscale/admin/pat. See app.tailscale.audit for the
    check catalog.
    """
    report = await TailscaleAuditor(
        ssh, _store(request), _admin_store(request),
    ).run()
    logger.info(
        "tailscale.audit",
        username=user.username,
        score=report.score, grade=report.grade,
        fail=report.fail_count, warn=report.warn_count, pass_=report.pass_count,
    )
    return {
        "score": report.score,
        "grade": report.grade,
        "pass_count": report.pass_count,
        "fail_count": report.fail_count,
        "warn_count": report.warn_count,
        "generated_at": report.generated_at.isoformat(),
        "raw_summary": report.raw_summary,
        "findings": [
            {
                "id": f.id, "label": f.label,
                "status": f.status, "severity": f.severity,
                "evidence": f.evidence,
                "recommendation": f.recommendation,
                "fix_available": f.fix_available,
            }
            for f in report.findings
        ],
    }


@router.post("/audit/fix")
async def fix_audit_finding(
    finding_id: str,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Apply the controller's auto-fix for a specific Tailscale audit
    finding. Only a small set of finding ids are auto-fixable (see
    ``TAILSCALE_FIXABLE_IDS`` in the audit module) — the rest require
    admin-console actions (ACL edits, device approval, MagicDNS toggle).
    """
    from app.tailscale.audit import TAILSCALE_FIXABLE_IDS
    from app.tailscale.client import TailscaleClient

    if finding_id not in TAILSCALE_FIXABLE_IDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Pas de fix automatique pour « {finding_id} » — voir la note "
                "de remediation pour l'action requise."
            ),
        )

    client = TailscaleClient(ssh)
    ok = False
    message = ""

    if finding_id in {"daemon_running", "uci_enable"}:
        # Both fixes converge on the same action : ensure UCI says enable
        # AND the procd service is up. Idempotent.
        try:
            r = await ssh.run(
                "uci -q set tailscale.@tailscale[0].enabled=1 && "
                "uci commit tailscale && "
                "/etc/init.d/tailscale enable >/dev/null 2>&1; "
                "/etc/init.d/tailscale start 2>&1 && echo OK",
                timeout=20,
            )
            ok = r.exit_status == 0 and "OK" in (r.stdout or "")
            message = (r.stdout or "")[-300:] or "Daemon démarré"
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"SSH error: {exc}"
    elif finding_id == "shields_up":
        # tailscale set --shields-up=true via the existing client helper.
        ok, errors = await client.apply_overrides(shields_up=True)
        message = "shields_up activé" if ok else "; ".join(errors)

    logger.info(
        "tailscale.audit.fix",
        username=user.username, finding=finding_id, ok=ok,
    )
    return {"ok": ok, "finding_id": finding_id, "message": message}


class HAConfigPatch(BaseModel):
    enabled: bool | None = None
    candidates: list[str] | None = None
    check_interval_seconds: int | None = None
    failsafe_mode: str | None = None  # "fail_open" | "keep"


@router.get("/ha")
async def get_ha(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Full HA state (config + last-tick runtime)."""
    return await _ha_store(request).get()


@router.post("/ha")
async def set_ha(
    body: HAConfigPatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Patch enabled / candidates / interval. Watchdog picks it up next tick."""
    if body.check_interval_seconds is not None and not (
        MIN_CHECK_INTERVAL <= body.check_interval_seconds <= MAX_CHECK_INTERVAL
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"check_interval_seconds must be in [{MIN_CHECK_INTERVAL}, {MAX_CHECK_INTERVAL}]",
        )
    if body.failsafe_mode is not None and body.failsafe_mode not in FAILSAFE_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"failsafe_mode must be one of {list(FAILSAFE_MODES)}",
        )
    new_state = await _ha_store(request).update_config(
        enabled=body.enabled,
        candidates=body.candidates,
        check_interval_seconds=body.check_interval_seconds,
        failsafe_mode=body.failsafe_mode,
    )
    logger.info(
        "tailscale.ha.config_updated",
        username=user.username,
        enabled=new_state["enabled"],
        candidates=new_state["candidates"],
        interval=new_state["check_interval_seconds"],
    )
    return new_state


class AdminPatRequest(BaseModel):
    pat: str
    tailnet: str | None = None  # "-" by default ⇒ token's home tailnet


@router.get("/admin/pat")
async def get_admin_pat_status(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Return PAT configuration metadata (NEVER the PAT itself)."""
    return await _admin_store(request).get_metadata()


@router.post("/admin/pat")
async def set_admin_pat(
    body: AdminPatRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Store a Tailscale admin PAT after verifying it against the API.

    The PAT is encrypted at rest. We refuse to store an invalid token —
    a 401 from /devices means the user typed something wrong (or used
    an OAuth client secret instead of an API access token).
    """
    pat = body.pat.strip()
    if not pat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pat required",
        )
    # Tailscale credentials all start with `tskey-` but only `-api-` works as
    # a Bearer token. Detect mismatch BEFORE round-tripping to api.tailscale.com
    # so the user gets a useful message instead of a generic 401.
    if pat.startswith("tskey-auth-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Ce token est une AUTH KEY (sert à enrôler des devices). "
                "Il faut un API access token: admin.tailscale.com → "
                "Settings → Keys → onglet 'API access tokens' → "
                "Generate access token. Le préfixe sera tskey-api-..."
            ),
        )
    if pat.startswith("tskey-client-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Ce token est un OAUTH CLIENT SECRET. Il nécessite un "
                "client_credentials exchange avant utilisation (pas géré "
                "pour l'instant). Génère plutôt un 'API access token' "
                "(tskey-api-...) dans admin.tailscale.com → Settings → Keys."
            ),
        )
    tailnet = (body.tailnet or "-").strip() or "-"
    try:
        async with TailscaleAdminAPI(pat, tailnet) as api:
            who = await api.whoami()
    except TailscaleAdminAPIError as exc:
        hint = ""
        if exc.status_code == 401:
            hint = (
                " — Token rejeté. Causes fréquentes: (1) token expiré "
                "(régénérer dans admin.tailscale.com → Keys), "
                "(2) copié avec espace/retour de ligne en début/fin, "
                "(3) scopes insuffisants si scoped token (cocher devices:read)."
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PAT validation failed: {exc}{hint}",
        ) from exc
    detected_tailnet = who.get("tailnet") or tailnet
    await _admin_store(request).save(pat, detected_tailnet)
    logger.info(
        "tailscale.admin_pat.saved",
        username=user.username,
        tailnet=detected_tailnet,
        devices=who.get("device_count", 0),
    )
    return {
        "configured": True,
        "tailnet": detected_tailnet,
        "device_count": who.get("device_count", 0),
    }


@router.delete("/admin/pat", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_pat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await _admin_store(request).clear()
    logger.warning("tailscale.admin_pat.cleared", username=user.username)


class PingRequest(BaseModel):
    target: str
    mode: str = "icmp"   # "icmp" or "tailscale"
    count: int = 3


class PingResponse(BaseModel):
    ok: bool
    output: str
    target: str
    mode: str


class TracerouteRequest(BaseModel):
    target: str
    max_hops: int = 15


class TracerouteResponse(BaseModel):
    ok: bool
    output: str
    target: str
    max_hops: int


@router.post("/traceroute", response_model=TracerouteResponse)
async def traceroute(
    body: TracerouteRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> TracerouteResponse:
    """Trace L3 path from the Slate to a target (busybox-style traceroute).

    Useful complement to ping when reachability fails — shows where the
    packet gets dropped (e.g. did it leave the Slate? did it reach the
    Tailscale exit? did it die in the home firewall?).
    """
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target required",
        )
    ok, output = await TailscaleClient(ssh).traceroute(target, max_hops=body.max_hops)
    logger.info(
        "tailscale.traceroute",
        username=user.username, target=target, max_hops=body.max_hops, ok=ok,
    )
    return TracerouteResponse(
        ok=ok, output=output, target=target, max_hops=body.max_hops,
    )


@router.post("/ping", response_model=PingResponse)
async def ping(
    body: PingRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> PingResponse:
    """Run an ICMP or Tailscale-overlay ping from the Slate to a target.

    Use case: after configuring Tailscale, verify that a peer / subnet route
    is actually reachable. ICMP mode works for any host; Tailscale mode
    reports the direct-vs-DERP relay path and is tailnet-specific.
    """
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target required",
        )
    mode = body.mode if body.mode in ("icmp", "tailscale") else "icmp"
    ok, output = await TailscaleClient(ssh).ping(target, mode=mode, count=body.count)
    logger.info(
        "tailscale.ping",
        username=user.username, target=target, mode=mode, ok=ok,
    )
    return PingResponse(ok=ok, output=output, target=target, mode=mode)
