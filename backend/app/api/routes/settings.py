"""App-level settings endpoints.

For Phase 1 we expose just the SSH keypair management. Future sections
(Proton creds, SIEM forward, scheduler) will land alongside.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import get_device_store, get_slate_ssh, get_ssh_keypair_store
from app.devices.store import DeviceStore
from app.auth import User, get_current_user
from app.settings.ssh_keys import (
    SSHKeypairStatus,
    SSHKeypairStore,
)
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


# The /settings/ssh-keypair routes manage the **default** device's
# keypair (singleton-shaped UX for V1). Per-device routes will move
# under /api/devices/{slug}/ssh-keypair once multi-device routing lands.
async def _default_device_slug(store: DeviceStore) -> str:
    """Resolve the default device's slug. 503 if no device is registered yet."""
    row = await store.get_default()
    if row is None:
        # Fallback : pick the lowest-id device (mirrors lifespan boot logic).
        # If there is none at all → 503.
        rows = await store.list_all()
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="no device registered — adopt a Slate first",
            )
        return rows[0].slug
    return row.slug


# ---------------------------- Pydantic IO ---------------------------- #


class SSHKeypairStatusResponse(BaseModel):
    generated: bool
    public_openssh: str | None
    fingerprint_sha256: str | None
    created_at: datetime | None
    deployed_to_slate: bool
    deployed_at: datetime | None
    auth_mode: str = Field(description="Current backend→Slate auth: 'key' or 'password'.")

    @classmethod
    def of(cls, status: SSHKeypairStatus, *, auth_mode: str) -> SSHKeypairStatusResponse:
        return cls(
            generated=status.generated,
            public_openssh=status.public_openssh,
            fingerprint_sha256=status.fingerprint_sha256,
            created_at=status.created_at,
            deployed_to_slate=status.deployed_to_slate,
            deployed_at=status.deployed_at,
            auth_mode=auth_mode,
        )


class DeployRequest(BaseModel):
    """Optional knobs for `POST .../deploy`."""

    disable_password_auth: bool = Field(
        default=False,
        description=(
            "If True, also flip `dropbear.PasswordAuth=off` after pushing the "
            "public key. SSH falls back to key-only. Only safe once the key is "
            "verified to work, otherwise you can lock yourself out."
        ),
    )


class DeployResponse(BaseModel):
    deployed: bool
    password_auth_disabled: bool
    note: str


# ---------------------------- endpoints ---------------------------- #


@router.get("/ssh-keypair", response_model=SSHKeypairStatusResponse)
async def get_ssh_keypair_status(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store)],
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
) -> SSHKeypairStatusResponse:
    """Return whether a keypair exists for the default device and whether it's been deployed."""
    slug = await _default_device_slug(device_store)
    status_ = await store.get_status(slug)
    return SSHKeypairStatusResponse.of(status_, auth_mode=ssh.auth_mode)


@router.post(
    "/ssh-keypair/generate",
    response_model=SSHKeypairStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_ssh_keypair(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store)],
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
) -> SSHKeypairStatusResponse:
    """Generate a fresh Ed25519 keypair, replacing any existing one.

    Does NOT push it to the Slate — caller must POST .../deploy next.
    The previous private key is discarded; the new one is Fernet-encrypted.
    """
    slug = await _default_device_slug(device_store)
    keypair = await store.generate_and_store(slug)
    logger.info(
        "settings.ssh_keypair.generated",
        device=slug, fingerprint=keypair.fingerprint_sha256,
    )
    status_ = await store.get_status(slug)
    return SSHKeypairStatusResponse.of(status_, auth_mode=ssh.auth_mode)


@router.post("/ssh-keypair/deploy", response_model=DeployResponse)
async def deploy_ssh_keypair(
    body: DeployRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store)],
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
) -> DeployResponse:
    """Push the public key to the Slate's authorized_keys, then switch to key auth.

    Requires the current SSH channel to be working (uses it to push the key).
    Optionally disables password auth on the Slate side.
    """
    slug = await _default_device_slug(device_store)
    status_ = await store.get_status(slug)
    if not status_.generated or not status_.public_openssh:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no keypair generated — POST /ssh-keypair/generate first",
        )

    pub = status_.public_openssh.strip()
    # Append the pubkey if not already present.
    # Dropbear reads authorized_keys from /etc/dropbear/authorized_keys on OpenWrt.
    escaped = pub.replace("'", "'\\''")
    cmd = (
        f"mkdir -p /etc/dropbear && touch /etc/dropbear/authorized_keys && "
        f"grep -qF '{escaped}' /etc/dropbear/authorized_keys || "
        f"echo '{escaped}' >> /etc/dropbear/authorized_keys && "
        f"chmod 600 /etc/dropbear/authorized_keys && echo OK"
    )
    try:
        result = await ssh.run(cmd)
    except SlateSSHError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH push failed: {exc}",
        ) from exc
    if result.exit_status != 0 or "OK" not in result.stdout:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"authorized_keys write failed: {result.stderr or result.stdout}",
        )

    await store.mark_deployed(slug)

    # Now switch our SSH channel to use the private key.
    private_pem = await store.get_private_pem(slug)
    if private_pem is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="private key vanished between deploy and switch",
        )
    await ssh.use_private_key(private_pem)

    # Sanity-check the new channel by running a trivial command.
    try:
        check = await ssh.run("echo OK")
    except SlateSSHError as exc:
        # Revert to password — we don't want to be locked out.
        await ssh.use_private_key(None)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"key-based SSH failed sanity check: {exc} — reverted to password",
        ) from exc
    if check.exit_status != 0 or "OK" not in check.stdout:
        await ssh.use_private_key(None)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="key-based SSH sanity check returned unexpected output",
        )

    password_auth_disabled = False
    if body.disable_password_auth:
        try:
            await ssh.run(
                "uci set dropbear.@dropbear[0].PasswordAuth=off && "
                "uci commit dropbear && /etc/init.d/dropbear restart"
            )
            password_auth_disabled = True
        except SlateSSHError as exc:
            # Key still works; just couldn't disable password. Don't fail.
            logger.warning("settings.ssh_keypair.disable_password_failed", error=str(exc))

    logger.info(
        "settings.ssh_keypair.deployed",
        password_auth_disabled=password_auth_disabled,
    )
    return DeployResponse(
        deployed=True,
        password_auth_disabled=password_auth_disabled,
        note=(
            "key-based SSH actif"
            + (
                ", auth password désactivée sur le Slate"
                if password_auth_disabled
                else ", auth password reste active sur le Slate"
            )
        ),
    )


@router.get(
    "/ssh-keypair/private-key",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"application/x-pem-file": {}},
            "description": "OpenSSH-format Ed25519 private key (sensitive — backup only).",
        },
        404: {"description": "No keypair generated yet."},
    },
)
async def export_ssh_private_key(
    user: Annotated[User, Depends(get_current_user)],
    store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store)],
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
) -> PlainTextResponse:
    """Download the private key PEM. Auth-protected, audit-logged.

    Use case: cold backup before disabling password auth on the Slate.
    The returned file matches what `ssh-keygen` would produce — usable with
    `ssh -i <file> root@<slate>`.
    """
    slug = await _default_device_slug(device_store)
    pem = await store.get_private_pem(slug)
    if pem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no keypair to export",
        )
    logger.warning(
        "settings.ssh_keypair.private_key_exported",
        username=user.username,
    )
    return PlainTextResponse(
        content=pem,
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": 'attachment; filename="slate-id_ed25519"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/ssh-keypair", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_ssh_keypair(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store)],
    device_store: Annotated[DeviceStore, Depends(get_device_store)],
) -> None:
    """Delete the keypair from our DB and revert SSH channel to password auth.

    Note: does NOT remove the public key from the Slate's authorized_keys.
    Do that manually via SSH if you really want to revoke (`vi /etc/dropbear/authorized_keys`).
    """
    slug = await _default_device_slug(device_store)
    deleted = await store.revoke(slug)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no keypair to revoke",
        )
    await ssh.use_private_key(None)
    logger.info("settings.ssh_keypair.revoked", device=slug)


# ---- Controller URLs ----
# Where the Slate can reach US (for the button-hook callback, etc.).

class ControllerUrlsBody(BaseModel):
    tailscale_url: str | None = None
    lan_url: str | None = None
    preferred: str | None = None  # "tailscale" | "lan"


def _controller_urls_store(request: Request):
    from app.db.database import make_session_factory
    from app.settings.controller_urls import ControllerUrlsStore
    sf = make_session_factory(request.app.state.db_engine)
    return ControllerUrlsStore(sf)


@router.get("/controller-urls")
async def get_controller_urls(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Current Tailscale + LAN URLs configured for Slate→Controller callbacks."""
    return await _controller_urls_store(request).get()


@router.post("/controller-urls")
async def set_controller_urls(
    body: ControllerUrlsBody,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Update one or both URLs + the preferred resolution order."""
    try:
        new_state = await _controller_urls_store(request).save(
            tailscale_url=body.tailscale_url,
            lan_url=body.lan_url,
            preferred=body.preferred,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    logger.info(
        "settings.controller_urls.saved",
        username=user.username,
        tailscale=new_state["tailscale_url"],
        lan=new_state["lan_url"],
        preferred=new_state["preferred"],
    )
    return new_state


# ---- Slate communication preferences ----

class SlateCommsBody(BaseModel):
    show_screen_messages: bool | None = None


def _slate_comms_store(request: Request):
    from app.db.database import make_session_factory
    from app.settings.slate_comms import SlateCommsStore
    sf = make_session_factory(request.app.state.db_engine)
    return SlateCommsStore(sf)


@router.get("/slate-comms")
async def get_slate_comms(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return await _slate_comms_store(request).get()


@router.post("/slate-comms")
async def set_slate_comms(
    body: SlateCommsBody,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    new_state = await _slate_comms_store(request).save(
        show_screen_messages=body.show_screen_messages,
    )
    logger.info(
        "settings.slate_comms.saved",
        username=user.username,
        show_screen_messages=new_state["show_screen_messages"],
    )
    return new_state


# ---- Reset-button profile cycle ----
# Short press of the reset button (< 3s) cycles through this list on the
# Slate. The cycle runs 100% locally on the device (slate-ctrl + handlers,
# no controller round-trip), so it works offline / on the road / when the
# controller is down. We just store the ordered list here and push it to
# /etc/slate-controller/cycle.json via the agent sync.


class ButtonCycleBody(BaseModel):
    steps: list[dict] = Field(
        default_factory=list,
        description=(
            "Ordered list of cycle steps. Each item is "
            '{kind: "profile"|"action", name: str}.\n'
            'Examples: {"kind":"profile","name":"mission"} or '
            '{"kind":"action","name":"update"}.\n'
            "Empty list disables the cycle (button-press becomes a logged"
            " no-op on the Slate)."
        ),
    )


def _button_cycle_store(request: Request):
    from app.db.database import make_session_factory
    from app.settings.button_cycle import ButtonCycleStore
    sf = make_session_factory(request.app.state.db_engine)
    return ButtonCycleStore(sf)


@router.get("/button-cycle")
async def get_button_cycle(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    steps = await _button_cycle_store(request).get()
    return {"steps": [s.model_dump() for s in steps]}


@router.put("/button-cycle")
async def set_button_cycle(
    body: ButtonCycleBody,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Save the cycle list AND push it to the Slate immediately.

    Pushes `cycle.json` to /etc/slate-controller/cycle.json on the Slate
    so the next button press uses the new list right away — no waiting
    for the next full /api/agent/sync.
    """
    from app.settings.button_cycle import CycleStep
    from app.slate_agent.sync import sync_button_cycle

    typed = [CycleStep.model_validate(s) for s in body.steps]
    saved = await _button_cycle_store(request).save(typed)
    # Look up the currently active profile so the rendered frames carry
    # an "ACTIVE" badge on the matching row. Falls back to None if the
    # profile store isn't ready or there's no active profile yet.
    from app.api.deps import get_profile_store
    try:
        store = get_profile_store(request)
        active_name = await store.get_active_name()
    except Exception:  # noqa: BLE001
        active_name = None
    pushed = False
    push_error: str | None = None
    try:
        rep = await sync_button_cycle(ssh, saved, active_name=active_name)
        pushed = rep.ok
        if rep.errors:
            push_error = "; ".join(rep.errors)
    except Exception as exc:  # noqa: BLE001 - never break the save on a sync hiccup
        push_error = f"sync_button_cycle crashed: {exc}"
    logger.info(
        "settings.button_cycle.saved",
        username=user.username, count=len(saved),
        kinds=[s.kind for s in saved],
        pushed=pushed, push_error=push_error,
    )
    return {
        "steps": [s.model_dump() for s in saved],
        "pushed_to_slate": pushed,
        "push_error": push_error,
    }



# ---------------------------- tailnet admin ---------------------------- #


class TailnetAdminBody(BaseModel):
    """PUT body for /settings/tailnet-admin-ips.

    Pass the full list ; empty list = no whitelist (every tailnet peer
    can reach the admin surface when profile.admin_only=true). The
    actual firewall enforcement only happens when at least one profile
    has admin_only=true active.
    """

    admin_ips: list[str] = Field(default_factory=list, max_length=32)


def _tailnet_admin_store(request: Request):
    from app.db.database import make_session_factory
    from app.settings.tailnet_admin import TailnetAdminStore
    sf = make_session_factory(request.app.state.db_engine)
    return TailnetAdminStore(sf)


@router.get("/tailnet-admin-ips")
async def get_tailnet_admin_ips(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Current whitelist of tailnet peers that can reach admin surface.

    The list drives the firewall rules generated when a profile has
    tailscale.admin_only=true. Empty = no whitelist (everyone passes
    even with admin_only ; the flag effectively becomes a no-op until
    the user adds at least one entry).
    """
    return await _tailnet_admin_store(request).get()


@router.put("/tailnet-admin-ips")
async def set_tailnet_admin_ips(
    body: TailnetAdminBody,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Replace the admin-IPs whitelist.

    Validation is light here (shape only) ; the agent-side handler is
    the authoritative validator since it issues the actual UCI commands.
    Changes only take effect on the next /api/agent/deploy (or
    profile re-activation).
    """
    try:
        saved = await _tailnet_admin_store(request).save(body.admin_ips)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        ) from exc
    logger.info(
        "settings.tailnet_admin.saved",
        username=user.username, count=len(saved["admin_ips"]),
    )
    return saved
