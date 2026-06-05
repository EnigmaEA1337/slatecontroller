"""PCAP capture endpoints — Phase 1 LAN tcpdump on the Slate.

  GET    /api/network/pcap                List the active device's captures
  POST   /api/network/pcap                Start a capture (returns the row)
  GET    /api/network/pcap/{id}           Refresh + return status
  POST   /api/network/pcap/{id}/stop      Kill the running tcpdump
  GET    /api/network/pcap/{id}/download  Stream the pcap binary
  DELETE /api/network/pcap/{id}           Cancel + remove
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import get_device_connections, get_slate_ssh
from app.auth import User, get_current_user
from app.devices.registry import DeviceConnections
from app.slate.ssh import SlateSSH
from app.wifi.pcap_capture import (
    ALLOWED_IFACES,
    DEFAULT_SNAPLEN,
    MAX_DURATION_S,
    MAX_SNAPLEN,
    MIN_DURATION_S,
    MIN_SNAPLEN,
    PcapCaptureManager,
    PcapStartSpec,
    to_view,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/network/pcap", tags=["network", "pcap"])


class PcapStartBody(BaseModel):
    iface: str = Field(min_length=1)
    duration_s: int = Field(
        default=30, ge=MIN_DURATION_S, le=MAX_DURATION_S,
    )
    snaplen: int = Field(
        default=DEFAULT_SNAPLEN, ge=MIN_SNAPLEN, le=MAX_SNAPLEN,
    )
    filter_expr: str = Field(default="", max_length=512)
    label: str = Field(default="", max_length=128)


def _mgr(request: Request) -> PcapCaptureManager:
    mgr = getattr(request.app.state, "pcap_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pcap manager not initialised",
        )
    return mgr


@router.get("")
async def list_captures(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    rows = await _mgr(request).list_for(conn.slug)
    return {
        "captures": [to_view(r) for r in rows],
        "allowed_ifaces": list(ALLOWED_IFACES),
        "limits": {
            "min_duration_s": MIN_DURATION_S,
            "max_duration_s": MAX_DURATION_S,
            "min_snaplen": MIN_SNAPLEN,
            "max_snaplen": MAX_SNAPLEN,
            "default_snaplen": DEFAULT_SNAPLEN,
        },
    }


@router.post("")
async def start_capture(
    body: PcapStartBody,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    try:
        row = await _mgr(request).start(
            slug=conn.slug, ssh=ssh,
            spec=PcapStartSpec(
                iface=body.iface,
                duration_s=body.duration_s,
                snaplen=body.snaplen,
                filter_expr=body.filter_expr,
                label=body.label,
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    logger.info(
        "pcap.start", username=user.username, device=conn.slug,
        capture_id=row.id, iface=body.iface, duration_s=body.duration_s,
    )
    return to_view(row)


@router.get("/{capture_id}")
async def get_capture(
    capture_id: int,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    row = await _mgr(request).refresh_status(conn.slug, ssh, capture_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"capture {capture_id} not found",
        )
    return to_view(row)


@router.post("/{capture_id}/stop")
async def stop_capture(
    capture_id: int,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    row = await _mgr(request).stop(conn.slug, ssh, capture_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"capture {capture_id} not found",
        )
    logger.info(
        "pcap.stop", username=user.username, device=conn.slug,
        capture_id=capture_id,
    )
    return to_view(row)


@router.get("/{capture_id}/download")
async def download_capture(
    capture_id: int,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        data = await _mgr(request).download(conn.slug, ssh, capture_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"capture {capture_id} not found",
        )
    return Response(
        content=data,
        media_type="application/vnd.tcpdump.pcap",
        headers={
            "Content-Disposition": (
                f'attachment; filename="slate-pcap-{capture_id}.pcap"'
            ),
        },
    )


@router.delete("/{capture_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_capture(
    capture_id: int,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    ok = await _mgr(request).delete(conn.slug, ssh, capture_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"capture {capture_id} not found",
        )
    logger.info(
        "pcap.delete", username=user.username, device=conn.slug,
        capture_id=capture_id,
    )
