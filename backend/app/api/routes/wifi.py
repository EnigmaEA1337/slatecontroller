"""Wi-Fi SSID catalog endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import get_wifi_store
from app.auth import User, get_current_user
from app.wifi.models import WifiSsidCreate, WifiSsidPublic, WifiSsidWrite
from app.wifi.qr import build_wifi_qr_string, render_qr_png
from app.wifi.store import (
    WifiSsidDuplicateError,
    WifiSsidError,
    WifiSsidNotFoundError,
    WifiSsidStore,
)
from app.wifi.suggestions import (
    SsidSuggestionsLibrary,
    get_suggestions_library,
)


class WifiPasswordResponse(BaseModel):
    """Reveal the stored password for an SSID. Use sparingly."""

    slug: str
    password: str


logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi", tags=["wifi"])


@router.get("/suggestions", response_model=SsidSuggestionsLibrary)
async def list_suggestions(
    _user: Annotated[User, Depends(get_current_user)],
) -> SsidSuggestionsLibrary:
    """Return the cyberpunk SSID name suggestion library.

    Sourced from `backend/data/ssid_suggestions.yaml`. Cached per process.
    """
    return get_suggestions_library()


@router.get("", response_model=list[WifiSsidPublic])
async def list_ssids(
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[WifiSsidPublic]:
    return await store.list_all()


@router.get("/{slug}", response_model=WifiSsidPublic)
async def get_ssid(
    slug: str,
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> WifiSsidPublic:
    try:
        return await store.get(slug)
    except WifiSsidNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSID {slug!r} not found",
        ) from exc


@router.post("", response_model=WifiSsidPublic, status_code=status.HTTP_201_CREATED)
async def create_ssid(
    body: WifiSsidCreate,
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> WifiSsidPublic:
    try:
        ssid = await store.create(body)
    except WifiSsidDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"SSID {str(exc)!r} already exists",
        ) from exc
    except WifiSsidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    logger.info("wifi.ssid.created", slug=ssid.slug)
    return ssid


@router.put("/{slug}", response_model=WifiSsidPublic)
async def update_ssid(
    slug: str,
    body: WifiSsidWrite,
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> WifiSsidPublic:
    try:
        ssid = await store.update(slug, body)
    except WifiSsidNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSID {slug!r} not found",
        ) from exc
    logger.info("wifi.ssid.updated", slug=slug)
    return ssid


@router.get("/{slug}/qr")
async def get_ssid_qr(
    slug: str,
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Return a PNG of the WiFi QR for this SSID.

    The PSK is embedded in the image but never returned as text by this route.
    """
    try:
        ssid = await store.get(slug)
        password = await store.get_password(slug)
    except WifiSsidNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSID {slug!r} not found",
        ) from exc

    payload = build_wifi_qr_string(
        ssid_name=ssid.ssid_name,
        security=ssid.security,
        password=password,
    )
    png = render_qr_png(payload)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{slug}/password", response_model=WifiPasswordResponse)
async def reveal_ssid_password(
    slug: str,
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> WifiPasswordResponse:
    """Return the decrypted PSK (auth-protected; for copy-to-clipboard UX)."""
    try:
        password = await store.get_password(slug)
    except WifiSsidNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSID {slug!r} not found",
        ) from exc
    return WifiPasswordResponse(slug=slug, password=password)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ssid(
    slug: str,
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await store.delete(slug)
    except WifiSsidNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSID {slug!r} not found",
        ) from exc
    logger.info("wifi.ssid.deleted", slug=slug)
