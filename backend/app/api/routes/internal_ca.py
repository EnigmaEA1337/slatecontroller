"""HTTP routes for the internal CA.

Surface :

  GET    /api/settings/internal-ca                       global status snapshot
  GET    /api/settings/internal-ca/config                current CA config
  PUT    /api/settings/internal-ca/config                update config
  GET    /api/settings/internal-ca/profiles              list RGS profile presets
  POST   /api/settings/internal-ca/init                  generate Root CA (idempotent)
  POST   /api/settings/internal-ca/regenerate            wipe + regen Root CA (destructive)
  GET    /api/settings/internal-ca/root-cert             download Root CA PEM
  GET    /api/settings/internal-ca/issued                list issued leaf certs
  POST   /api/settings/internal-ca/issued                issue a fresh leaf
  POST   /api/settings/internal-ca/issued/{serial}/revoke    mark revoked
  POST   /api/settings/internal-ca/issued/{serial}/push      push to Slate uhttpd
  GET    /api/settings/internal-ca/issued/{serial}/cert      download leaf cert PEM
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from urllib.parse import urlparse

from app.api.deps import get_device_store, get_slate_ssh
from app.devices.store import DeviceStore
from app.auth import User, get_current_user
from app.settings.internal_ca import (
    CAConfig,
    IssuedCertSummary,
    RGS_PROFILES,
    get_root_cert_pem,
    get_slate_cert_metadata,
    init_root_ca,
    is_initialized,
    issue_cert,
    list_issued_certs,
    load_ca_config,
    push_slate_cert,
    revoke_cert,
    save_ca_config,
)
from app.settings.internal_ca.pki import (
    parse_root_ca_details,
    regenerate_root_ca,
)
from app.settings.internal_ca.state import get_issued_materials
from app.slate.ssh import SlateSSH

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/settings/internal-ca", tags=["settings"])


# ---------- View models ----------

class CAStatusView(BaseModel):
    initialized: bool
    config: CAConfig
    profile_keys: list[str]
    issued_count: int
    slate_cert_serial_hex: str | None
    slate_cert_pushed_at: str | None


class IssueRequest(BaseModel):
    """Cert issuance is bound to a known subject — devices (today) or
    user/role identities (future). Free-form CN+SANs is rejected as a
    policy guard against issuing certs for arbitrary names.

    The frontend selector resolves a subject id (`device:<slug>`) to
    its registered admin hostnames + IPs ; the operator can optionally
    extend that with additional SANs (e.g. a custom mDNS alias) but
    cannot remove the baseline ones tied to the equipment record."""

    subject_id: str = Field(
        ...,
        description=(
            "Stable identifier from /subjects, e.g. 'device:slate'. "
            "Must match an existing equipment row."
        ),
    )
    additional_sans: list[str] = Field(
        default_factory=list,
        description="Optional extra SANs added on top of the registered ones.",
    )


class WriteResponse(BaseModel):
    ok: bool
    message: str
    serial_hex: str | None = None


class RegenerateRequest(BaseModel):
    confirm: bool = Field(
        ...,
        description=(
            "Must be True. Regenerating the Root CA invalidates every "
            "issued leaf cert (chain broken) and forces a re-install of "
            "the Root CA on every personal device. Operator confirms in "
            "the UI before this fires."
        ),
    )


# ---------- Read endpoints ----------

@router.get("", response_model=CAStatusView)
async def read_status(
    _user: Annotated[User, Depends(get_current_user)],
) -> CAStatusView:
    cfg = load_ca_config()
    issued = list_issued_certs()
    slate_meta = get_slate_cert_metadata() or {}
    return CAStatusView(
        initialized=is_initialized(),
        config=cfg,
        profile_keys=list(RGS_PROFILES.keys()),
        issued_count=len(issued),
        slate_cert_serial_hex=slate_meta.get("serial_hex"),
        slate_cert_pushed_at=slate_meta.get("pushed_at"),
    )


@router.get("/config", response_model=CAConfig)
async def read_config(
    _user: Annotated[User, Depends(get_current_user)],
) -> CAConfig:
    return load_ca_config()


@router.get("/profiles", response_model=dict[str, CAConfig])
async def list_profiles(
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, CAConfig]:
    return RGS_PROFILES


# ---------- Write endpoints ----------

@router.put("/config", response_model=CAConfig)
async def update_config(
    body: CAConfig,
    user: Annotated[User, Depends(get_current_user)],
) -> CAConfig:
    """Persist a new config. Affects FUTURE issuance only — Root CA + already
    issued leaf certs are immutable. To rebuild the CA itself, call /regenerate."""
    save_ca_config(body)
    logger.info("internal_ca.config.updated", username=user.username, profile=body.profile_label)
    return body


@router.post("/init", response_model=CAStatusView)
async def init_ca(
    user: Annotated[User, Depends(get_current_user)],
) -> CAStatusView:
    """Generate the Root CA using the current persisted config.

    Idempotent : if already initialized, returns the existing state
    without regenerating. To force a fresh CA call /regenerate.
    """
    cfg = load_ca_config()
    init_root_ca(cfg)
    logger.info("internal_ca.init.ok", username=user.username)
    return await read_status(_user=user)


@router.post("/regenerate", response_model=CAStatusView)
async def regenerate_ca(
    body: RegenerateRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CAStatusView:
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmation required to wipe the existing Root CA",
        )
    cfg = load_ca_config()
    regenerate_root_ca(cfg)
    logger.warning("internal_ca.regenerated", username=user.username)
    return await read_status(_user=user)


@router.get("/root-cert")
async def download_root_cert(
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Public Root CA PEM, served as an attachment for install on user devices.

    The CA private key is never exposed by any endpoint.
    """
    try:
        pem = get_root_cert_pem()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Root CA not initialized",
        ) from exc
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="trust-controller-root-ca.pem"'},
    )


class IssuanceSubject(BaseModel):
    """An entity the operator is allowed to issue a cert for.

    Today we expose only devices ; future iterations can add user
    identities (S/MIME, client-cert mTLS) and infrastructure roles
    (controller itself, scheduler workers) here.
    """

    kind: str = Field(..., description="'device' for now ; 'user' / 'role' later")
    id: str = Field(..., description="stable identifier (device slug)")
    label: str = Field(..., description="human-readable display name")
    suggested_common_name: str
    suggested_sans: list[str]
    notes: str = ""


def _extract_host(url: str) -> str | None:
    """Pull the hostname out of an admin URL string (https://... or ip:port)."""
    if "://" not in url:
        url = "https://" + url
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


@router.get("/subjects", response_model=list[IssuanceSubject])
async def list_subjects(
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[IssuanceSubject]:
    """Enumerate equipments + identities for which the operator can issue
    a cert. The UI feeds this into a selector — no free-form CN input.

    For each device, the suggested SAN list comes from its `admin_urls`
    (parsed hostnames + IP literals) ; the CN defaults to the first
    hostname-style entry, falling back to the device slug if all admin
    URLs are bare IPs.
    """
    devices = await device_store.list_all()
    out: list[IssuanceSubject] = []
    for d in devices:
        hosts: list[str] = []
        seen: set[str] = set()
        for url in d.admin_urls:
            h = _extract_host(url)
            if h and h not in seen:
                hosts.append(h)
                seen.add(h)
        if d.host and d.host not in seen:
            hosts.insert(0, d.host)
            seen.add(d.host)
        # Pick CN : first non-IP hostname, else device slug
        cn = next(
            (h for h in hosts if not all(c.isdigit() or c == "." for c in h)),
            d.slug,
        )
        out.append(
            IssuanceSubject(
                kind="device",
                id=d.slug,
                label=d.label or d.slug,
                suggested_common_name=cn,
                suggested_sans=hosts,
                notes=f"{d.model} · status={d.status}",
            )
        )
    return out


@router.get("/details", response_model=dict)
async def read_root_details(
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Full X.509 parse of the active Root CA (Subject, Issuer, serial,
    public key, signature, fingerprints, extensions, PEM). Used by the
    UI's "Détails du CA" panel — equivalent of `openssl x509 -text` in
    a structured form. Returns 404 if no Root CA is on disk yet."""
    if not is_initialized():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Root CA not initialized",
        )
    return parse_root_ca_details()


@router.get("/issued", response_model=list[IssuedCertSummary])
async def issued_list(
    _user: Annotated[User, Depends(get_current_user)],
) -> list[IssuedCertSummary]:
    return list_issued_certs()


@router.post("/issued", response_model=IssuedCertSummary)
async def issue_new(
    body: IssueRequest,
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> IssuedCertSummary:
    if not is_initialized():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Root CA not initialized — call /init first",
        )

    # Resolve the subject id back to a known equipment row.
    if not body.subject_id.startswith("device:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unsupported subject kind in '{body.subject_id}' — only "
                "'device:<slug>' is allowed today"
            ),
        )
    slug = body.subject_id.removeprefix("device:")
    device = await device_store.get_by_slug(slug)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown device slug : {slug!r}",
        )

    # Recompute the CN + baseline SANs from the live equipment record.
    # We don't trust whatever the client sent — the only operator-controlled
    # input is `additional_sans`.
    hosts: list[str] = []
    seen: set[str] = set()
    for url in device.admin_urls:
        h = _extract_host(url)
        if h and h not in seen:
            hosts.append(h)
            seen.add(h)
    if device.host and device.host not in seen:
        hosts.insert(0, device.host)
        seen.add(device.host)
    if not hosts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"device {slug!r} has no admin URLs — register at least one "
                "before issuing a cert"
            ),
        )
    common_name = next(
        (h for h in hosts if not all(c.isdigit() or c == "." for c in h)),
        slug,
    )
    final_sans = list(hosts)
    for s in body.additional_sans:
        if s and s not in final_sans:
            final_sans.append(s)

    cfg = load_ca_config()
    try:
        summary, _cert_pem, _key_pem = issue_cert(
            config=cfg,
            common_name=common_name,
            sans=final_sans,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "internal_ca.issued.api",
        username=user.username,
        subject_id=body.subject_id,
        cn=common_name,
        sans=final_sans,
        serial_hex=summary.serial_hex,
    )
    return summary


@router.post("/issued/{serial_hex}/revoke", response_model=IssuedCertSummary)
async def revoke_issued(
    serial_hex: str,
    user: Annotated[User, Depends(get_current_user)],
) -> IssuedCertSummary:
    try:
        summary = revoke_cert(serial_hex)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("internal_ca.revoked", username=user.username, serial_hex=serial_hex)
    return summary


@router.post("/issued/{serial_hex}/push", response_model=WriteResponse)
async def push_to_slate(
    serial_hex: str,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> WriteResponse:
    try:
        msg = await push_slate_cert(ssh, serial_hex)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("internal_ca.push.api", username=user.username, serial_hex=serial_hex)
    return WriteResponse(ok=True, message=msg, serial_hex=serial_hex)


@router.get("/issued/{serial_hex}/cert")
async def download_issued_cert(
    serial_hex: str,
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        cert_pem, _ = get_issued_materials(serial_hex)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=cert_pem,
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": f'attachment; filename="cert-{serial_hex[:16]}.pem"',
        },
    )
