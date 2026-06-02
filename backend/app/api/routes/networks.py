"""Networks (bridges / VLANs) endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_network_store, get_slate_ssh
from app.auth import User, get_current_user
from app.networks.diag import collect_diag
from app.networks.models import NetworkCreate, NetworkPublic, NetworkWrite
from app.networks.speedtest import (
    PublicIPInfo,
    SpeedtestResult,
    fetch_active_bridges,
    fetch_active_ssids,
    fetch_public_ip,
    run_speedtest,
)
from app.networks.store import (
    NetworkDuplicateError,
    NetworkError,
    NetworkNotFoundError,
    NetworkStore,
)
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/networks", tags=["networks"])


@router.get("", response_model=list[NetworkPublic])
async def list_networks(
    store: Annotated[NetworkStore, Depends(get_network_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[NetworkPublic]:
    return await store.list_all()


@router.get("/diag")
async def network_diag(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Live L2/L3 diagnostic snapshot from the Slate.

    Returns interfaces (with addresses + traffic counters), IPv4/IPv6
    routing tables, ARP/NDP neighbours, and OpenWrt logical interfaces.
    Read-only: no command modifies state.
    """
    try:
        return await collect_diag(ssh)
    except SlateSSHError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH a échoué: {exc}",
        ) from exc


@router.get("/active-bridges")
async def list_active_bridges(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Bridges currently forwarding (kernel truth) — used by the Dashboard
    to distinguish "catalogued" from "actually carrying traffic".
    """
    bridges = await fetch_active_bridges(ssh)
    return {"bridges": bridges, "count": len(bridges)}


@router.get("/active-ssids")
async def list_active_ssids(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """SSIDs currently broadcasting (kernel-derived, MTK-cache-safe).

    Each entry carries the iface, ssid, band (2g/5g/6g) and the bridge
    it's attached to. The Dashboard counts these and groups them by
    band for the "radios" satellite.
    """
    items = await fetch_active_ssids(ssh)
    return {
        "ssids": [
            {
                "ifname": s.ifname, "ssid": s.ssid,
                "band": s.band, "bridge": s.bridge,
            }
            for s in items
        ],
        "count": len(items),
    }


@router.get("/public-ip")
async def get_public_ip(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """The public IP + country + ISP the Slate's WAN currently exits with.

    Runs ``curl https://ipinfo.io/json`` on the device — so the answer
    reflects what the Slate sees, not the controller. Useful for the
    Dashboard's "at a glance" hub-and-spoke widget.
    """
    info: PublicIPInfo = await fetch_public_ip(ssh)
    return {
        "ip": info.ip,
        "country": info.country,
        "city": info.city,
        "region": info.region,
        "org": info.org,
        "latitude": info.latitude,
        "longitude": info.longitude,
    }


@router.post("/speedtest")
async def network_speedtest(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Run a ping + download + upload test against Cloudflare from the Slate.

    Synchronous (the UI shows a spinner) — takes ~20-30 s on a typical link.
    Sequential phases : ping first so it's not contended ; then a 100 MB
    download ; then a 20 MB upload. No extra packages needed, just curl.
    """
    res: SpeedtestResult = await run_speedtest(ssh)
    return {
        "ping_ms": res.ping_ms,
        "jitter_ms": res.jitter_ms,
        "packet_loss_pct": res.packet_loss_pct,
        "download_mbps": res.download_mbps,
        "upload_mbps": res.upload_mbps,
        "server": res.server,
        "bytes_downloaded": res.bytes_downloaded,
        "bytes_uploaded": res.bytes_uploaded,
        "error": res.error,
    }


@router.get("/{slug}", response_model=NetworkPublic)
async def get_network(
    slug: str,
    store: Annotated[NetworkStore, Depends(get_network_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> NetworkPublic:
    try:
        return await store.get(slug)
    except NetworkNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Network {slug!r} not found",
        ) from exc


@router.post("", response_model=NetworkPublic, status_code=status.HTTP_201_CREATED)
async def create_network(
    body: NetworkCreate,
    store: Annotated[NetworkStore, Depends(get_network_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> NetworkPublic:
    try:
        nw = await store.create(body)
    except NetworkDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Network {str(exc)!r} already exists",
        ) from exc
    except NetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    logger.info("network.created", slug=nw.slug)
    return nw


@router.put("/{slug}", response_model=NetworkPublic)
async def update_network(
    slug: str,
    body: NetworkWrite,
    store: Annotated[NetworkStore, Depends(get_network_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> NetworkPublic:
    try:
        nw = await store.update(slug, body)
    except NetworkNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Network {slug!r} not found",
        ) from exc
    logger.info("network.updated", slug=slug)
    return nw


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network(
    slug: str,
    store: Annotated[NetworkStore, Depends(get_network_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await store.delete(slug)
    except NetworkNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Network {slug!r} not found",
        ) from exc
    logger.info("network.deleted", slug=slug)
