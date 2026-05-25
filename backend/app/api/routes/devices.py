"""Device management endpoints (`/api/devices`).

For V1 the controller still binds singleton slate_client / slate_ssh /
adguard_manager to the device flagged `is_default=True`. Adding/adopting
a new device does NOT yet automatically swap the active device — the user
must mark it as default explicitly (and the backend will rebind on next
restart). Active per-request scoping comes later.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_slate_url_resolver
from app.auth import User, get_current_user
from app.devices.adoption import run_adoption
from app.devices.models import (
    AdoptionOptions,
    AdoptionRunReport,
    DeviceCreate,
    DevicePublic,
    DeviceUpdate,
    FactoryResetConfirm,
    FactoryResetReport,
)
from app.devices.store import DeviceStore, DeviceStoreError
from app.devices.tls import fetch_cert
from app.settings.ssh_keys import SSHKeypairStore
from app.slate.ssh import SlateSSH, SlateSSHError
from app.slate.url_resolver import SlateUrlResolver

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


def get_device_store(request: Request) -> DeviceStore:
    store: DeviceStore = request.app.state.device_store
    return store


def get_ssh_keypair_store_from_app(request: Request) -> SSHKeypairStore:
    store: SSHKeypairStore = request.app.state.ssh_keypair_store
    return store


async def _to_public(
    *, row, store: DeviceStore, keypair_store: SSHKeypairStore,
) -> DevicePublic:
    creds = await store.get_rpc_credentials(row.slug)
    username = creds[0] if creds else "root"
    has_kp = False
    deployed = False
    if row.is_default:
        # Only the default device has its keypair in app_secrets (V1).
        st = await keypair_store.get_status()
        has_kp = st.generated
        deployed = st.deployed_to_slate
    # Backfill admin_urls from `host` if empty (legacy rows or fresh-from-
    # adoption rows where the user hasn't customized the URLs yet).
    admin_urls = list(row.admin_urls or [])
    if not admin_urls and row.host:
        admin_urls = [_default_url_for(row)]
    return DevicePublic(
        id=row.id,
        slug=row.slug,
        label=row.label,
        model=row.model,
        host=row.host,
        admin_urls=admin_urls,
        rpc_port=row.rpc_port,
        rpc_scheme=row.rpc_scheme,
        ssh_port=row.ssh_port,
        rpc_username=username,
        tls_fingerprint_sha256=row.tls_fingerprint_sha256,
        status=row.status,
        is_default=row.is_default,
        notes=row.notes,
        last_probe_at=row.last_probe_at,
        adopted_at=row.adopted_at,
        created_at=row.created_at,
        has_ssh_keypair=has_kp,
        ssh_key_deployed=deployed,
    )


def _default_url_for(row) -> str:
    """Build the default admin URL from the row's host + rpc fields. Used
    to seed admin_urls when empty (legacy rows)."""
    scheme = row.rpc_scheme or "https"
    if row.rpc_port and row.rpc_port not in (80, 443):
        return f"{scheme}://{row.host}:{row.rpc_port}"
    return f"{scheme}://{row.host}"


def _extract_probe_host(url_or_host: str) -> str:
    """Extract a bare host from `https://host[:port][/path]` or a bare
    `host`. Strips scheme, port and path so we can hand it straight to
    `fetch_cert(host, port)` which opens a raw TCP socket."""
    raw = (url_or_host or "").strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.rstrip("/").split("/", 1)[0]
    if ":" in raw and not raw.startswith("["):
        raw = raw.split(":", 1)[0]
    return raw


@router.get("", response_model=list[DevicePublic])
async def list_devices(
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> list[DevicePublic]:
    rows = await store.list_all()
    return [
        await _to_public(row=row, store=store, keypair_store=keypair_store)
        for row in rows
    ]


@router.get("/{slug}", response_model=DevicePublic)
async def get_device(
    slug: str,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> DevicePublic:
    row = await store.get_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device {slug!r} not found")
    return await _to_public(row=row, store=store, keypair_store=keypair_store)


@router.post(
    "",
    response_model=DevicePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_device(
    body: DeviceCreate,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> DevicePublic:
    """Register a new device. Does NOT run adoption tasks — call /adopt next."""
    try:
        row = await store.create(
            slug=body.slug,
            label=body.label or body.host,
            model=body.model,
            host=body.host,
            rpc_port=body.rpc_port,
            rpc_scheme=body.rpc_scheme,
            ssh_port=body.ssh_port,
            rpc_username=body.rpc_username,
            rpc_password=body.rpc_password,
            notes=body.notes,
            is_default=False,
        )
    except DeviceStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _to_public(row=row, store=store, keypair_store=keypair_store)


@router.patch("/{slug}", response_model=DevicePublic)
async def update_device(
    slug: str,
    body: DeviceUpdate,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
    resolver: Annotated[SlateUrlResolver, Depends(get_slate_url_resolver)],
) -> DevicePublic:
    updates = {
        "label": body.label,
        "host": body.host,
        "rpc_port": body.rpc_port,
        "ssh_port": body.ssh_port,
        "notes": body.notes,
    }
    # admin_urls: optional list. If provided, validate + normalize each
    # entry (trim, strip trailing /). Empty list is legal and reverts to
    # legacy single-host behavior (the _to_public layer backfills from host).
    if body.admin_urls is not None:
        normalized: list[str] = []
        for raw in body.admin_urls:
            value = (raw or "").strip().rstrip("/")
            if not value:
                continue
            normalized.append(value)
        updates["admin_urls"] = normalized
    try:
        row = await store.update_fields(slug, **updates)
        await store.update_credentials(
            slug,
            rpc_username=body.rpc_username,
            rpc_password=body.rpc_password,
        )
    except DeviceStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # If admin_urls changed on the default device, hot-swap the resolver
    # so SSH calls use the new list immediately (no backend restart needed).
    if "admin_urls" in updates and row.is_default and updates["admin_urls"]:
        try:
            await resolver.set_urls(updates["admin_urls"])
            await resolver.force_refresh()
        except (ValueError, RuntimeError) as exc:
            logger.warning("device.update.resolver_refresh_failed", error=str(exc))
    return await _to_public(row=row, store=store, keypair_store=keypair_store)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    slug: str,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
) -> None:
    row = await store.get_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device {slug!r} not found")
    if row.is_default:
        raise HTTPException(
            status_code=400,
            detail="cannot delete the default device — mark another as default first",
        )
    deleted = await store.delete(slug)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"device {slug!r} not found")


@router.post("/{slug}/probe", response_model=DevicePublic)
async def probe_device(
    slug: str,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> DevicePublic:
    """Test connectivity: fetch TLS cert + try SSH login. Updates status/fingerprint.

    The TLS probe tries every URL in `admin_urls` in order (LAN, Tailscale,
    custom) and uses the first reachable one. This matches what the SSH
    layer does via `SlateUrlResolver` — without it, the probe would fail
    on the legacy `host` field whenever the LAN is down, falsely marking
    the device as `error` even though it's reachable via Tailscale.
    """
    row = await store.get_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device {slug!r} not found")
    creds = await store.get_rpc_credentials(slug)
    if creds is None:
        raise HTTPException(status_code=400, detail="device has no stored credentials")
    username, password = creds

    # Build the list of host candidates to try, in order. For the default
    # device, lead with the resolver's current active URL so we don't waste
    # a timeout on a candidate we already know is down. Other admin_urls
    # come next as fallbacks. Final fallback is the legacy `host` field.
    candidates: list[str] = []
    if row.is_default:
        try:
            resolver = request.app.state.slate_url_resolver
            active = await resolver.active()
            candidates.append(_extract_probe_host(active))
        except Exception:  # noqa: BLE001
            pass  # resolver missing — fall back to admin_urls below
    for url in row.admin_urls or []:
        h = _extract_probe_host(url)
        if h and h not in candidates:
            candidates.append(h)
    if row.host and row.host not in candidates:
        candidates.append(row.host)

    # TLS reachability + fingerprint — try each candidate until one answers.
    fingerprint = ""
    last_exc: Exception | None = None
    probed_host: str | None = None
    for host_try in candidates:
        try:
            info = await fetch_cert(host_try, row.rpc_port)
            fingerprint = info.fingerprint_sha256
            probed_host = host_try
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    if fingerprint == "":
        await store.mark_probed(slug, status="error")
        raise HTTPException(
            status_code=502,
            detail=(
                f"TLS probe failed on all {len(candidates)} candidate URL(s): "
                f"{last_exc}"
            ),
        )

    # If we already pinned a different cert, alert.
    if row.tls_fingerprint_sha256 and row.tls_fingerprint_sha256 != fingerprint:
        await store.mark_probed(slug, status="error", tls_fingerprint_sha256=fingerprint)
        raise HTTPException(
            status_code=409,
            detail=(
                f"TLS fingerprint mismatch — stored={row.tls_fingerprint_sha256[:23]}… "
                f"got={fingerprint[:23]}… (possible MITM or device re-flashed)"
            ),
        )

    # SSH reachability — reuse the app singleton for the default device (it
    # already carries the keypair if deployed). Open a fresh password-auth
    # channel for non-default devices (they haven't been adopted yet).
    needs_close = False
    if row.is_default:
        ssh: SlateSSH = request.app.state.slate_ssh
    else:
        ssh = SlateSSH(
            slate_url=f"{row.rpc_scheme}://{row.host}",
            username=username,
            password=password,
            port=row.ssh_port,
        )
        needs_close = True
    try:
        result = await ssh.run("echo OK")
    except SlateSSHError as exc:
        await store.mark_probed(slug, status="error", tls_fingerprint_sha256=fingerprint)
        raise HTTPException(status_code=502, detail=f"SSH probe failed: {exc}") from exc
    finally:
        if needs_close:
            await ssh.close()

    if "OK" not in result.stdout:
        await store.mark_probed(slug, status="error", tls_fingerprint_sha256=fingerprint)
        raise HTTPException(
            status_code=502,
            detail=f"SSH echo returned unexpected output: {result.stdout!r}",
        )

    # All good — pin fingerprint + restore the right status :
    #   - if previously adopted (or adopted_at is set), stay/return to "adopted"
    #     even if the previous probe had transiently marked us "error"
    #     because the LAN was down. This is the whole point of the resolver
    #     failover : a transient outage doesn't require re-adoption.
    #   - else (never adopted yet) → "pending" to invite the adoption flow.
    if row.status == "adopted" or row.adopted_at is not None:
        new_status = "adopted"
    else:
        new_status = "pending"
    await store.mark_probed(slug, status=new_status, tls_fingerprint_sha256=fingerprint)
    row = await store.get_by_slug(slug)
    assert row is not None
    return await _to_public(row=row, store=store, keypair_store=keypair_store)


@router.post("/{slug}/default", response_model=DevicePublic)
async def set_default(
    slug: str,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> DevicePublic:
    """Mark this device as default. Takes effect after backend restart (V1).

    The singleton SlateClient/SlateSSH/AdGuardManager are bound at startup;
    rebinding them at runtime is multi-device V2 work.
    """
    try:
        await store.set_default(slug)
    except DeviceStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    row = await store.get_by_slug(slug)
    assert row is not None
    return await _to_public(row=row, store=store, keypair_store=keypair_store)


@router.post("/{slug}/adopt", response_model=AdoptionRunReport)
async def adopt_device(
    slug: str,
    body: AdoptionOptions,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> AdoptionRunReport:
    """Run the selected hardening tasks against this device.

    For the default device we reuse the app's existing SlateSSH (already
    using stored keypair if deployed). For non-default devices we open a
    fresh SSH channel from the stored creds.
    """
    row = await store.get_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device {slug!r} not found")

    if row.is_default and hasattr(request.app.state, "slate_ssh"):
        ssh: SlateSSH = request.app.state.slate_ssh
    else:
        creds = await store.get_rpc_credentials(slug)
        if creds is None:
            raise HTTPException(status_code=400, detail="device has no stored credentials")
        username, password = creds
        ssh = SlateSSH(
            slate_url=f"{row.rpc_scheme}://{row.host}",
            username=username,
            password=password,
            port=row.ssh_port,
        )

    try:
        report = await run_adoption(
            device_slug=slug,
            host=row.host,
            rpc_port=row.rpc_port,
            options=body,
            ssh=ssh,
            store=store,
            keypair_store=keypair_store,
        )
    finally:
        if not row.is_default:
            await ssh.close()

    logger.info(
        "devices.adopt", slug=slug, overall=report.overall_status,
    )
    return report


@router.post("/{slug}/forget", response_model=DevicePublic)
async def forget_device(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
    keypair_store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store_from_app)],
) -> DevicePublic:
    """Reset the device's local adoption state without touching the hardware.

    The Slate keeps every config it received during adoption (SSH key-only,
    forced HTTPS, UPnP off, TLS pin). Only the controller's notion of
    "adopted" is cleared, so the UI presents the adoption flow again.

    Use when:
      - You want to re-run hardening with different options.
      - You upgraded the controller and want a clean slate on this side.
      - Adoption finished partial and you want to retry from scratch.

    If you want to actually wipe the Slate, use `/factory-reset` instead.
    """
    try:
        await store.mark_forgotten(slug)
    except DeviceStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    row = await store.get_by_slug(slug)
    assert row is not None
    logger.info("devices.forget", slug=slug, username=user.username)
    return await _to_public(row=row, store=store, keypair_store=keypair_store)


@router.post("/{slug}/factory-reset", response_model=FactoryResetReport)
async def factory_reset_device(
    slug: str,
    body: FactoryResetConfirm,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    store: Annotated[DeviceStore, Depends(get_device_store)],
) -> FactoryResetReport:
    """**DESTRUCTIVE** — wipe the Slate and reboot it to factory defaults.

    Runs `firstboot -y && sync && reboot &` via SSH. This:
      - Erases everything written under `/overlay` (= every UCI change,
        installed packages, dropbear keys, AdGuard config, profiles, etc.).
      - Reboots the Slate, which comes back up with the OEM stock config
        (DHCP server on 192.168.8.1, default admin password, etc.).
      - Leaves the controller blind: TLS fingerprint, SSH key, RPC
        credentials are all invalidated.

    Safety:
      - Requires `confirm_slug` in the body matching the device slug
        exactly (GitHub-style typed confirmation).
      - The controller's local DB is reset to `pending` so the UI invites
        the operator to re-probe + re-adopt from scratch.

    The reboot is fired in the background (`reboot &`) so SSH doesn't hang
    on the closing connection; the call returns as soon as the command is
    accepted, not when the Slate is back up.
    """
    if body.confirm_slug != slug:
        raise HTTPException(
            status_code=400,
            detail=(
                "Confirmation slug mismatch — type the device slug exactly "
                "to confirm the factory reset."
            ),
        )
    row = await store.get_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"device {slug!r} not found")

    # Get an SSH channel: reuse the app's singleton if this is the default
    # device (already authenticated key-only); otherwise build an ad-hoc one
    # from the device's stored credentials.
    if row.is_default:
        ssh: SlateSSH = request.app.state.slate_ssh
        owns_ssh = False
    else:
        creds = await store.get_rpc_credentials(slug)
        if creds is None:
            raise HTTPException(
                status_code=400, detail="device has no stored credentials",
            )
        username, password = creds
        ssh = SlateSSH(
            slate_url=f"{row.rpc_scheme}://{row.host}",
            username=username,
            password=password,
            port=row.ssh_port,
        )
        owns_ssh = True

    # `firstboot -y` is non-interactive; backgrounding `reboot` lets the
    # SSH session close cleanly before the box drops the link.
    cmd = "firstboot -y && sync && (sleep 1; reboot) >/dev/null 2>&1 &"
    note = "firstboot accepted; reboot in progress"
    started = True
    try:
        result = await ssh.run(cmd, timeout=20)
        if result.exit_status != 0:
            started = False
            note = f"firstboot returned {result.exit_status}: {result.stderr.strip()[:120]}"
    except SlateSSHError as exc:
        started = False
        note = f"SSH error: {exc}"
    finally:
        if owns_ssh:
            await ssh.close()

    # Mark as forgotten regardless: the Slate is rebooting, the local state
    # is stale either way. If the reset failed mid-flight, the operator
    # can re-probe to see whether the Slate came back as factory or not.
    try:
        await store.mark_forgotten(slug)
    except DeviceStoreError:
        pass

    logger.warning(
        "devices.factory_reset",
        slug=slug, username=user.username, started=started, note=note,
    )
    return FactoryResetReport(device_slug=slug, started=started, note=note)
