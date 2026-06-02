"""Wi-Fi SSID catalog endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import get_slate_ssh, get_wifi_store
from app.auth import User, get_current_user
from app.slate.ssh import SlateSSH
from app.wifi.discovery import discover_wireless, slugify_ssid_name
from app.wifi.models import WifiSsidCreate, WifiSsidPublic, WifiSsidWrite
from app.wifi.slate_state import WifiSlotState, get_slate_wifi_state
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


class WifiSlotStateView(BaseModel):
    """Pydantic mirror of `WifiSlotState` for the API surface."""

    section_name: str
    ifname: str
    band: str | None
    mode: str
    ssid_uci: str
    ssid_broadcast: str | None
    enabled: bool
    network: str
    encryption: str
    is_up: bool
    slot_kind: str
    marker: bool
    notes: list[str]


@router.get("/slate-state", response_model=list[WifiSlotStateView])
async def read_slate_wifi_state(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[WifiSlotStateView]:
    """Live diagnostic of every WiFi slot on the Slate.

    Cross-references `uci show wireless` (persisted config) with `iwinfo`
    (runtime broadcast state). Used by the WiFi UI's "État live" panel
    so the operator can spot drift between controller intent and Slate
    reality without SSH'ing manually.
    """
    try:
        slots = await get_slate_wifi_state(ssh)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return [
        WifiSlotStateView(
            section_name=s.section_name,
            ifname=s.ifname,
            band=s.band,
            mode=s.mode,
            ssid_uci=s.ssid_uci,
            ssid_broadcast=s.ssid_broadcast,
            enabled=s.enabled,
            network=s.network,
            encryption=s.encryption,
            is_up=s.is_up,
            slot_kind=s.slot_kind,
            marker=s.marker,
            notes=s.notes,
        )
        for s in slots
    ]


@router.get("/suggestions", response_model=SsidSuggestionsLibrary)
async def list_suggestions(
    _user: Annotated[User, Depends(get_current_user)],
) -> SsidSuggestionsLibrary:
    """Return the cyberpunk SSID name suggestion library.

    Sourced from `backend/data/ssid_suggestions.yaml`. Cached per process.
    """
    return get_suggestions_library()


class DiscoverReport(BaseModel):
    """Outcome of a Slate→catalog import. Per-SSID status so the UI can
    show a clear "imported / already known / failed" line for each."""

    found_on_slate: int
    imported: list[WifiSsidPublic]
    skipped_slugs: list[str]   # slug already existed — left untouched
    errors: list[str]


@router.post("/discover-from-slate", response_model=DiscoverReport)
async def discover_from_slate(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> DiscoverReport:
    """Probe `uci show wireless` on the Slate and import every broadcast
    SSID into the controller's catalog.

    Idempotent — entries whose slug already exists are NOT clobbered
    (the user may have customised the broadcast name, password, network
    binding, etc. ; we don't want to silently revert their edits).
    """
    try:
        discovered = await discover_wireless(ssh)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"discovery failed: {exc}",
        ) from exc

    imported: list[WifiSsidPublic] = []
    skipped: list[str] = []
    errors: list[str] = []
    for d in discovered:
        slug = slugify_ssid_name(d.ssid_name)
        try:
            await store.get(slug)
            skipped.append(slug)
            continue
        except WifiSsidNotFoundError:
            pass
        try:
            created = await store.create(
                WifiSsidCreate(
                    slug=slug,
                    ssid_name=d.ssid_name,
                    bands=list(d.bands),
                    # MLO can't be inferred from `uci show wireless`
                    # alone (would need to peek at mtwifi's `mld_id`
                    # extension fields). Start as plain multi-VAP and
                    # let the user re-tick if it was a Wi-Fi 7 MLO.
                    mlo=False,
                    security=d.security,
                    hidden=d.hidden,
                    # password : we can't read it from the Slate without
                    # extra UCI digging (and even then it'd be plaintext in
                    # uci show — privacy concern). Imported entries start
                    # password-less and the user enters it once in the UI.
                    password=None,
                    # network binding is NOT imported onto the SSID — it's
                    # a per-profile decision now. We record the discovered
                    # bridge in the notes for reference only.
                    notes=(
                        f"imported from Slate "
                        f"(ifaces={','.join(d.ifaces)}, was on network={d.network})"
                    ),
                )
            )
            imported.append(created)
        except WifiSsidDuplicateError:
            # Race with another call — treat like skip.
            skipped.append(slug)
        except WifiSsidError as exc:
            errors.append(f"{slug}: {exc}")

    logger.info(
        "wifi.discover_from_slate",
        found=len(discovered),
        imported=len(imported), skipped=len(skipped), errors=len(errors),
    )
    return DiscoverReport(
        found_on_slate=len(discovered),
        imported=imported,
        skipped_slugs=skipped,
        errors=errors,
    )


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
