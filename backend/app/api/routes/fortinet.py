"""REST API for Fortinet SSL VPN management.

  CRUD on ``/api/vpn/fortinet`` — stored configs (host, port, user, pw,
  trusted_cert pin, CA PEM, notes).

  Operational endpoints :
    POST /{slug}/connect  body {otp}  → spawn openfortivpn on the Slate
    POST /disconnect                  → terminate the active tunnel
    GET  /status                      → live SSH probe (state, iface, IPs,
                                        rx/tx bytes, uptime)

OTP is NEVER persisted. It comes in on the request body, is forwarded to
the agent, and forgotten on the next line.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_slate_ssh
from app.auth import User, get_current_user
from app.db.database import make_session_factory
from app.slate.ssh import SlateSSH
from app.vpn.fortinet.manager import FortinetManager, FortinetManagerError
from app.vpn.fortinet.models import (
    FortinetConfigCreate,
    FortinetConfigPublic,
    FortinetConfigUpdate,
    FortinetConnectRequest,
    FortinetLogsResponse,
    FortinetStatus,
)
from app.vpn.fortinet.store import (
    FortinetConfigStore,
    FortinetDuplicateError,
    FortinetNotFoundError,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/vpn/fortinet", tags=["vpn-fortinet"])


# ---------------------------- dependencies ---------------------------- #


def _store(request: Request) -> FortinetConfigStore:
    sf = make_session_factory(request.app.state.db_engine)
    return FortinetConfigStore(sf)


def _manager(request: Request, ssh: SlateSSH) -> FortinetManager:
    return FortinetManager(ssh=ssh, store=_store(request))


async def _to_public(
    row, *, has_password: bool,
) -> FortinetConfigPublic:
    return FortinetConfigPublic(
        slug=row.slug,
        display_name=row.display_name or "",
        gateway_host=row.gateway_host,
        gateway_port=row.gateway_port,
        username=row.username,
        trusted_cert_sha256=row.trusted_cert_sha256 or "",
        has_ca_cert=bool((row.ca_cert_pem or "").strip()),
        has_password=has_password,
        notes=row.notes or "",
        last_status=row.last_status or "unknown",
        last_connected_at=row.last_connected_at,
        last_disconnected_at=row.last_disconnected_at,
        last_error=row.last_error or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------- CRUD ---------------------------- #


@router.get("", response_model=list[FortinetConfigPublic])
async def list_configs(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> list[FortinetConfigPublic]:
    store = _store(request)
    rows = await store.list_all()
    result: list[FortinetConfigPublic] = []
    for r in rows:
        has_pw = await store.has_password(r.slug)
        result.append(await _to_public(r, has_password=has_pw))
    return result


from pydantic import BaseModel


class PreflightResponse(BaseModel):
    ok: bool
    binary: str = ""
    version: str = ""
    ppp_kmod: bool = False
    error: str = ""


class BuildArtifactResponse(BaseModel):
    available: bool
    path: str = ""
    size_bytes: int = 0
    sha256: str = ""
    version: str = ""
    git_ref: str = ""
    built_at_seconds: int = 0


class BuildRequest(BaseModel):
    """Optional git ref the operator wants to build (default v1.21.0)."""

    openfortivpn_ref: str = "v1.21.0"


class BuildResponse(BaseModel):
    ok: bool
    rc: int
    logs: str
    artifact: BuildArtifactResponse


class SideloadResponse(BaseModel):
    ok: bool
    remote_path: str
    size_bytes: int
    version: str
    sha256: str


@router.get("/preflight", response_model=PreflightResponse)
async def preflight_endpoint(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> PreflightResponse:
    """Probe whether the Slate has openfortivpn + ppp ready. The UI
    surfaces ``ok=False`` with the install instructions so the operator
    doesn't waste time trying to connect before sideloading the binary."""
    mgr = _manager(request, ssh)
    info = await mgr.preflight()
    return PreflightResponse(**info)


@router.get("/logs", response_model=FortinetLogsResponse)
async def get_logs(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    lines: int = 200,
) -> FortinetLogsResponse:
    """Tail openfortivpn's runtime log on the Slate (`/var/log/openfortivpn.log`).

    Used by the mobile connect page to surface auth failures / SSL pin
    mismatches / SAML redirect prompts that would otherwise stay hidden
    on the Slate. ``lines`` caps the response — default 200 fits in a
    phone scroll without flooding the UI. Capped server-side to 1000.
    """
    from app.slate.ssh import SlateSSHError
    from app.vpn.fortinet.manager import LOGFILE

    n = max(1, min(int(lines), 1000))
    try:
        r = await ssh.run(
            f"wc -l < {LOGFILE} 2>/dev/null ; tail -n {n} {LOGFILE} 2>/dev/null",
            timeout=10,
        )
    except SlateSSHError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH error reading logs: {exc}",
        ) from exc
    raw = (r.stdout or "").splitlines()
    if not raw:
        return FortinetLogsResponse(lines=[], truncated=False)
    try:
        total = int(raw[0].strip())
    except (ValueError, IndexError):
        total = len(raw)
    body_lines = raw[1:]
    return FortinetLogsResponse(
        lines=body_lines,
        truncated=total > len(body_lines),
    )


@router.get("/status", response_model=FortinetStatus)
async def get_status_endpoint(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FortinetStatus:
    """Live tunnel state — SSH-probes the Slate, no DB cache."""
    mgr = _manager(request, ssh)
    try:
        return await mgr.status()
    except FortinetManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.post(
    "",
    response_model=FortinetConfigPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_config(
    body: FortinetConfigCreate,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> FortinetConfigPublic:
    store = _store(request)
    try:
        row = await store.create(
            slug=body.slug,
            display_name=body.display_name,
            gateway_host=body.gateway_host,
            gateway_port=body.gateway_port,
            username=body.username,
            password=body.password,
            trusted_cert_sha256=body.trusted_cert_sha256,
            ca_cert_pem=body.ca_cert_pem,
            notes=body.notes,
        )
    except FortinetDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"config {str(exc)!r} already exists",
        ) from exc
    logger.info(
        "forti.config.created", slug=body.slug, has_password=bool(body.password),
    )
    return await _to_public(row, has_password=bool(body.password))


@router.get("/{slug}", response_model=FortinetConfigPublic)
async def get_config(
    slug: str,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> FortinetConfigPublic:
    store = _store(request)
    try:
        row = await store.get_by_slug(slug)
    except FortinetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"config {slug!r} not found",
        ) from exc
    has_pw = await store.has_password(slug)
    return await _to_public(row, has_password=has_pw)


@router.patch("/{slug}", response_model=FortinetConfigPublic)
async def update_config(
    slug: str,
    body: FortinetConfigUpdate,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> FortinetConfigPublic:
    store = _store(request)
    try:
        updates: dict = {
            "display_name": body.display_name,
            "gateway_host": body.gateway_host,
            "gateway_port": body.gateway_port,
            "username": body.username,
            "trusted_cert_sha256": body.trusted_cert_sha256,
            "ca_cert_pem": body.ca_cert_pem,
            "notes": body.notes,
        }
        row = await store.update_fields(slug, **updates)
        if body.password is not None:
            await store.set_password(slug, body.password)
    except FortinetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"config {slug!r} not found",
        ) from exc
    has_pw = await store.has_password(slug)
    logger.info(
        "forti.config.updated", slug=slug, password_changed=body.password is not None,
    )
    return await _to_public(row, has_password=has_pw)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    slug: str,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> None:
    store = _store(request)
    ok = await store.delete(slug)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"config {slug!r} not found",
        )
    logger.info("forti.config.deleted", slug=slug)


# ---------------------------- operational ---------------------------- #


async def _run_network_routing_reconcile(
    request: Request, ssh: SlateSSH,
) -> dict | None:
    """Best-effort post-connect/disconnect/edit hook : refresh the per-
    network egress rules so they match the new tunnel state. Returns the
    report on success, ``None`` on failure (logged, not raised — the
    caller's primary action already succeeded)."""
    from app.networks.store import NetworkStore
    from app.vpn.fortinet.network_routing import (
        FortiNetworkRoutingError,
        reconcile,
    )

    sf = make_session_factory(request.app.state.db_engine)
    try:
        return await reconcile(
            ssh=ssh,
            network_store=NetworkStore(sf),
            forti_manager=FortinetManager(ssh=ssh, store=_store(request)),
        )
    except FortiNetworkRoutingError as exc:
        logger.warning("forti.routing.reconcile_failed", error=str(exc))
        return None


@router.post("/{slug}/connect", response_model=FortinetStatus)
async def connect(
    slug: str,
    body: FortinetConnectRequest,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FortinetStatus:
    """Spawn openfortivpn on the Slate with the supplied OTP.

    The 25-second deadline matches the manager's polling window — when
    the gateway has a slow 2FA review (FortiToken push wait time), the
    operator should expect the request to take up to half a minute.

    Also fires a per-network egress reconcile after the tunnel comes up,
    so networks flagged ``egress_via_forti=True`` immediately switch to
    routing through ppp. Reconcile failure is non-fatal (the tunnel is up,
    just the routing hasn't been applied — operator can retry via the
    reconcile endpoint).
    """
    mgr = _manager(request, ssh)
    try:
        st = await mgr.connect(
            slug,
            body.otp,
            username_override=body.username,
            password_override=body.password,
        )
    except FortinetManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    await _run_network_routing_reconcile(request, ssh)
    return st


# /logs declared near the top of this module (just above /status) so the
# router doesn't shadow it with the catch-all /{slug} GET that lives below.


@router.post("/disconnect", response_model=FortinetStatus)
async def disconnect(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FortinetStatus:
    """Tear down the active tunnel AND reconcile per-network egress so
    the kill-switch REJECTs (where configured) become active and the
    SNAT/policy-routing entries are removed."""
    mgr = _manager(request, ssh)
    try:
        st = await mgr.disconnect()
    except FortinetManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    await _run_network_routing_reconcile(request, ssh)
    return st


@router.get("/build/artifact", response_model=BuildArtifactResponse)
async def get_build_artifact(
    _user: Annotated[User, Depends(get_current_user)],
) -> BuildArtifactResponse:
    """Cheap stat of the local artifact volume — used by the UI to decide
    whether the Sideload button should be active."""
    from app.vpn.fortinet.builder import get_artifact_status

    art = get_artifact_status()
    return BuildArtifactResponse(**art.__dict__)


@router.post("/build", response_model=BuildResponse)
async def build_binary_endpoint(
    body: BuildRequest,
    _user: Annotated[User, Depends(get_current_user)],
) -> BuildResponse:
    """Synchronously spawn the `slate-forti-builder` container to cross-
    compile a static aarch64-musl openfortivpn binary into the shared
    artifact volume.

    Runs 3-12 minutes on first call (openssl static + autotools chain),
    much faster on later calls (image already cached). Returns the
    container's exit code + tail of stdout/stderr so the UI can show
    build errors inline.
    """
    from app.vpn.fortinet.builder import (
        FortinetBuilderError,
        build_binary,
    )

    try:
        result = await build_binary(body.openfortivpn_ref)
    except FortinetBuilderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return BuildResponse(
        ok=result["ok"],
        rc=result["rc"],
        logs=result["logs"],
        artifact=BuildArtifactResponse(**result["artifact"]),
    )


@router.post("/build/sideload", response_model=SideloadResponse)
async def sideload_binary_endpoint(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> SideloadResponse:
    """SCP the locally-built binary to /usr/sbin/openfortivpn on the
    Slate and chmod 755. Idempotent."""
    from app.vpn.fortinet.builder import (
        FortinetBuilderError,
        sideload_binary,
    )

    try:
        result = await sideload_binary(ssh)
    except FortinetBuilderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return SideloadResponse(**result)


@router.post("/network-routing/reconcile")
async def reconcile_endpoint(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Explicit reconcile button. Use when :
      - a network was just edited and the auto-sync didn't run
      - the operator suspects drift after a fw3 reload or manual fiddle
      - testing a config without bouncing the tunnel.
    Idempotent and cheap."""
    report = await _run_network_routing_reconcile(request, ssh)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="reconcile failed — see backend logs",
        )
    return report
