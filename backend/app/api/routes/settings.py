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

from app.api.deps import get_slate_ssh, get_ssh_keypair_store
from app.auth import User, get_current_user
from app.settings.ssh_keys import (
    SSHKeypairStatus,
    SSHKeypairStore,
)
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


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
) -> SSHKeypairStatusResponse:
    """Return whether a keypair exists and whether it's been deployed."""
    status_ = await store.get_status()
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
) -> SSHKeypairStatusResponse:
    """Generate a fresh Ed25519 keypair, replacing any existing one.

    Does NOT push it to the Slate — caller must POST .../deploy next.
    The previous private key is discarded; the new one is Fernet-encrypted.
    """
    keypair = await store.generate_and_store()
    logger.info("settings.ssh_keypair.generated", fingerprint=keypair.fingerprint_sha256)
    status_ = await store.get_status()
    return SSHKeypairStatusResponse.of(status_, auth_mode=ssh.auth_mode)


@router.post("/ssh-keypair/deploy", response_model=DeployResponse)
async def deploy_ssh_keypair(
    body: DeployRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
    store: Annotated[SSHKeypairStore, Depends(get_ssh_keypair_store)],
) -> DeployResponse:
    """Push the public key to the Slate's authorized_keys, then switch to key auth.

    Requires the current SSH channel to be working (uses it to push the key).
    Optionally disables password auth on the Slate side.
    """
    status_ = await store.get_status()
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

    await store.mark_deployed()

    # Now switch our SSH channel to use the private key.
    private_pem = await store.get_private_pem()
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
) -> PlainTextResponse:
    """Download the private key PEM. Auth-protected, audit-logged.

    Use case: cold backup before disabling password auth on the Slate.
    The returned file matches what `ssh-keygen` would produce — usable with
    `ssh -i <file> root@<slate>`.
    """
    pem = await store.get_private_pem()
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
) -> None:
    """Delete the keypair from our DB and revert SSH channel to password auth.

    Note: does NOT remove the public key from the Slate's authorized_keys.
    Do that manually via SSH if you really want to revoke (`vi /etc/dropbear/authorized_keys`).
    """
    deleted = await store.revoke()
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no keypair to revoke",
        )
    await ssh.use_private_key(None)
    logger.info("settings.ssh_keypair.revoked")


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
