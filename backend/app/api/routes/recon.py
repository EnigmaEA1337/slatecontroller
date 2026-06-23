"""WAN/LAN reconnaissance — operator-triggered active discovery sweep.

Endpoints :
  - ``GET    /api/recon/interfaces``        — pickable interfaces
  - ``POST   /api/recon/scans``             — launch a scan
  - ``GET    /api/recon/scans``             — list scans for this device
  - ``GET    /api/recon/scans/{id}``        — scan + hosts + ports detail
  - ``POST   /api/recon/scans/{id}/cancel`` — cancel a running scan
  - ``DELETE /api/recon/scans/{id}``        — drop one scan (and rows)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections, get_slate_ssh
from app.auth import User, get_current_user
from app.devices.registry import DeviceConnections
from app.recon.interfaces import (
    ReconInterface,
    list_active_interfaces,
)
from app.recon.runner import ReconTaskRegistry, ScanRequest, run_scan
from app.recon.store import (
    STATUS_RUNNING,
    ReconStore,
)
from app.slate.ssh import SlateSSH

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/recon", tags=["recon"])


# ---------------------------- Pydantic views ---------------------------- #


class ReconInterfaceView(BaseModel):
    name: str
    ipv4_cidr: str
    family: str  # wan / lan / guest / other
    host_count: int
    scannable: bool
    gateway: str
    scan_cidr: str
    scan_clamped: bool

    @classmethod
    def from_dc(cls, i: ReconInterface) -> "ReconInterfaceView":
        return cls(
            name=i.name,
            ipv4_cidr=i.ipv4_cidr,
            family=i.family,
            host_count=i.host_count,
            scannable=i.scannable,
            gateway=i.gateway,
            scan_cidr=i.scan_cidr,
            scan_clamped=i.scan_clamped,
        )


class ReconLaunchRequest(BaseModel):
    interfaces: list[str] = Field(..., min_length=1, max_length=16)
    do_arp: bool = True
    do_ping: bool = True
    do_tcp: bool = True
    do_banner: bool = True


class ReconScanSummary(BaseModel):
    id: int
    status: Literal["running", "done", "failed", "cancelled"]
    progress: str
    error: str
    host_count: int
    port_count: int
    started_at: datetime
    finished_at: datetime | None
    scope: dict


class ReconHostView(BaseModel):
    interface: str
    ip: str
    mac: str
    vendor: str
    hostname: str
    source: str
    is_gateway: bool
    is_self: bool


class ReconPortView(BaseModel):
    ip: str
    port: int
    state: str
    banner: str
    service: str


class ReconScanDetail(ReconScanSummary):
    hosts: list[ReconHostView]
    ports: list[ReconPortView]


# ---------------------------- helpers ---------------------------- #


def _store(request: Request) -> ReconStore:
    sf: async_sessionmaker = request.app.state.db_session_factory
    return ReconStore(sf)


def _registry(request: Request) -> ReconTaskRegistry:
    """Lazy singleton recon task registry pinned to app.state."""
    reg = getattr(request.app.state, "recon_task_registry", None)
    if reg is None:
        reg = ReconTaskRegistry()
        request.app.state.recon_task_registry = reg
    return reg


def _row_to_summary(row) -> ReconScanSummary:
    try:
        scope = json.loads(row.scope_json or "{}")
    except json.JSONDecodeError:
        scope = {}
    return ReconScanSummary(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        progress=row.progress,
        error=row.error,
        host_count=row.host_count,
        port_count=row.port_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
        scope=scope,
    )


# ---------------------------- endpoints ---------------------------- #


@router.get("/interfaces", response_model=list[ReconInterfaceView])
async def get_interfaces(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[ReconInterfaceView]:
    """List L3 interfaces on the Slate — what the operator can scan."""
    ifaces = await list_active_interfaces(ssh)
    return [ReconInterfaceView.from_dc(i) for i in ifaces]


@router.post(
    "/scans",
    response_model=ReconScanSummary,
    status_code=status.HTTP_201_CREATED,
)
async def launch_scan(
    body: ReconLaunchRequest,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> ReconScanSummary:
    """Launch a new scan. Returns immediately ; client polls /scans/{id}."""
    available = await list_active_interfaces(ssh)
    by_name = {i.name: i for i in available}
    unknown = [n for n in body.interfaces if n not in by_name]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"interfaces inconnues : {', '.join(unknown)}",
        )
    store = _store(request)
    row = await store.create(
        device_slug=conn.slug,
        scope=body.model_dump(),
    )
    req = ScanRequest(
        interfaces=body.interfaces,
        do_arp=body.do_arp,
        do_ping=body.do_ping,
        do_tcp=body.do_tcp,
        do_banner=body.do_banner,
    )
    task = asyncio.create_task(run_scan(ssh, store, _registry(request), row.id, req))
    _registry(request).register(row.id, task)
    logger.info(
        "recon.scan.launched",
        username=user.username, device=conn.slug,
        scan_id=row.id, interfaces=body.interfaces,
    )
    return _row_to_summary(row)


@router.get("/scans", response_model=list[ReconScanSummary])
async def list_scans(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[ReconScanSummary]:
    rows = await _store(request).list(conn.slug)
    return [_row_to_summary(r) for r in rows]


@router.get("/scans/{scan_id}", response_model=ReconScanDetail)
async def get_scan(
    scan_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ReconScanDetail:
    store = _store(request)
    row = await store.get(scan_id)
    if row is None or row.device_slug != conn.slug:
        raise HTTPException(status_code=404, detail="scan inconnu")
    hosts = await store.hosts_for(scan_id)
    ports = await store.ports_for(scan_id)
    summary = _row_to_summary(row)
    return ReconScanDetail(
        **summary.model_dump(),
        hosts=[
            ReconHostView(
                interface=h.interface, ip=h.ip, mac=h.mac,
                vendor=h.vendor, hostname=h.hostname, source=h.source,
                is_gateway=h.is_gateway, is_self=h.is_self,
            )
            for h in hosts
        ],
        ports=[
            ReconPortView(
                ip=p.ip, port=p.port, state=p.state,
                banner=p.banner, service=p.service,
            )
            for p in ports
        ],
    )


@router.post("/scans/{scan_id}/cancel", response_model=ReconScanSummary)
async def cancel_scan(
    scan_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> ReconScanSummary:
    store = _store(request)
    row = await store.get(scan_id)
    if row is None or row.device_slug != conn.slug:
        raise HTTPException(status_code=404, detail="scan inconnu")
    if row.status != STATUS_RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"scan déjà en état '{row.status}', impossible d'annuler",
        )
    _registry(request).cancel(scan_id)
    # The task's CancelledError handler in run_scan marks the row
    # cancelled. Re-fetch to return the new state.
    row = await store.get(scan_id)
    logger.info(
        "recon.scan.cancelled",
        username=user.username, device=conn.slug, scan_id=scan_id,
    )
    assert row is not None
    return _row_to_summary(row)


@router.delete("/scans/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: int,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    store = _store(request)
    row = await store.get(scan_id)
    if row is None or row.device_slug != conn.slug:
        raise HTTPException(status_code=404, detail="scan inconnu")
    if row.status == STATUS_RUNNING:
        _registry(request).cancel(scan_id)
    await store.delete(scan_id)
    logger.info(
        "recon.scan.deleted",
        username=user.username, device=conn.slug, scan_id=scan_id,
    )
