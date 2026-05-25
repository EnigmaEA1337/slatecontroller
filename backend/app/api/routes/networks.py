"""Networks (bridges / VLANs) endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_network_store, get_slate_ssh
from app.auth import User, get_current_user
from app.networks.diag import collect_diag
from app.networks.models import NetworkCreate, NetworkPublic, NetworkWrite
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
