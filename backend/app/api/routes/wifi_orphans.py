"""WiFi orphan endpoints (Phase 2) — surface + delete UCI sections
that aren't managed by the controller."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_device_connections, get_slate_ssh
from app.auth import User, get_current_user
from app.devices.registry import DeviceConnections
from app.slate.ssh import SlateSSH
from app.wifi.orphans import WifiOrphan, delete_many, delete_orphan, list_orphans

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi/orphans", tags=["wifi", "orphans"])


class OrphanView(BaseModel):
    section: str
    type: str
    ssid: str
    encryption: str
    device: str
    network: str
    disabled: bool
    managed: bool
    extras: dict[str, str]


def _to_view(o: WifiOrphan) -> OrphanView:
    return OrphanView(
        section=o.section, type=o.type, ssid=o.ssid,
        encryption=o.encryption, device=o.device, network=o.network,
        disabled=o.disabled, managed=o.managed,
        extras={k: str(v) for k, v in o.extras.items()},
    )


@router.get("", response_model=list[OrphanView])
async def get_orphans(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[OrphanView]:
    """List every wifi-iface / wifi-mld section on the Slate that's NOT
    marked ``slate_ctrl_managed=1``."""
    try:
        orphans = await list_orphans(ssh)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    return [_to_view(o) for o in orphans]


@router.delete("/{section}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_one(
    section: str,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        ok = await delete_orphan(ssh, section)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"section {section!r} not found or already deleted",
        )
    logger.info(
        "wifi.orphan.deleted", username=user.username,
        device=conn.slug, section=section,
    )


class BulkDeleteBody(BaseModel):
    sections: list[str]


@router.post("/cleanup-all")
async def cleanup_all(
    body: BulkDeleteBody,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    """Delete a batch of orphan sections in one SSH round-trip. Returns
    ``{section: result}`` so the UI can show per-row outcomes."""
    try:
        result = await delete_many(ssh, body.sections)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    deleted = sum(1 for v in result.values() if v == "deleted")
    logger.info(
        "wifi.orphans.bulk_cleanup",
        username=user.username, device=conn.slug,
        requested=len(body.sections), deleted=deleted,
    )
    return result
