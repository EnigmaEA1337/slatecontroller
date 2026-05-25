"""VPN config upload & management endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_vpn_config_store
from app.auth import User, get_current_user
from app.models.vpn_config import VPNConfigPublic, VPNConfigUploadResponse, VpnProvider
from app.vpn.configs_store import (
    VPNConfigDuplicateError,
    VPNConfigError,
    VPNConfigNotFoundError,
    VPNConfigStore,
)
from app.vpn.wg_parser import WGConfigParseError, parse_wg_config

MAX_CONF_BYTES = 16 * 1024  # 16 KiB — real WG configs are well under 1 KiB

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/vpn/configs", tags=["vpn"])


@router.get("", response_model=list[VPNConfigPublic])
async def list_configs(
    store: Annotated[VPNConfigStore, Depends(get_vpn_config_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[VPNConfigPublic]:
    return await store.list_all()


@router.get("/{name}", response_model=VPNConfigPublic)
async def get_config(
    name: str,
    store: Annotated[VPNConfigStore, Depends(get_vpn_config_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> VPNConfigPublic:
    try:
        return await store.get(name)
    except VPNConfigNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config {name!r} not found",
        ) from exc


@router.post(
    "",
    response_model=VPNConfigUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_config(
    store: Annotated[VPNConfigStore, Depends(get_vpn_config_store)],
    _user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(description="WireGuard .conf file")],
    name: Annotated[str, Form(description="User-given identifier (will be slugged)")],
    provider: Annotated[VpnProvider, Form()] = "proton",
) -> VPNConfigUploadResponse:
    """Upload a WireGuard `.conf` file. The private key is stored encrypted."""
    raw = await file.read(MAX_CONF_BYTES + 1)
    if len(raw) > MAX_CONF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"config file too large (>{MAX_CONF_BYTES} bytes)",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="config must be UTF-8 text",
        ) from exc

    try:
        parsed = parse_wg_config(text)
    except WGConfigParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid WireGuard config: {exc}",
        ) from exc

    try:
        created = await store.add(name=name, provider=provider, config=parsed)
    except VPNConfigDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"config {str(exc)!r} already exists",
        ) from exc
    except VPNConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    logger.info(
        "vpn.config.uploaded",
        name=created.name,
        provider=created.provider,
        endpoint=created.peer_endpoint,
    )
    return VPNConfigUploadResponse(
        name=created.name,
        provider=created.provider,
        peer_endpoint=created.peer_endpoint,
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    name: str,
    store: Annotated[VPNConfigStore, Depends(get_vpn_config_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await store.delete(name)
    except VPNConfigNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config {name!r} not found",
        ) from exc
    logger.info("vpn.config.deleted", name=name)
