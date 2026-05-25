"""Profile CRUD + active-profile marker.

Phase 2a: DB-backed CRUD with a hybrid model — the 5 YAML templates are seeded
on first boot as `source="template"`, and the user can create/edit/duplicate
their own (`source="user"`). Applying a profile to the Slate is Phase 2b.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import (
    get_profile_store,
    get_slate_ssh,
    get_vpn_config_store,
    get_wifi_store,
)
from app.auth import User, get_current_user
from app.db.database import make_session_factory
from app.models.profile import Profile
from app.profiles.wallpapers import (
    ALLOWED_MIME,
    FIT_MODES,
    KINDS,
    MAX_BYTES,
    WallpaperError,
    WallpaperRecord,
    WallpaperStore,
)
from app.slate.ssh import SlateSSH
from app.profiles.applier import ProfileApplier
from app.profiles.scoring import ProfileScores, ScoreItem, compute_scores
from app.profiles.store import (
    ProfileDuplicateError,
    ProfileImmutableError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    StoredProfile,
)
from app.vpn.configs_store import VPNConfigStore
from app.wifi.store import WifiSsidStore

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/profiles", tags=["profiles"])


# ---------------------------- response models ---------------------------- #


class ScoreItemModel(BaseModel):
    name: str
    points: int
    max_points: int
    note: str = ""

    @classmethod
    def of(cls, item: ScoreItem) -> ScoreItemModel:
        return cls(
            name=item.name,
            points=item.points,
            max_points=item.max_points,
            note=item.note,
        )


class ProfileScoresModel(BaseModel):
    anonymization: int
    security: int
    breakdown_anonymization: list[ScoreItemModel]
    breakdown_security: list[ScoreItemModel]

    @classmethod
    def of(cls, scores: ProfileScores) -> ProfileScoresModel:
        return cls(
            anonymization=scores.anonymization,
            security=scores.security,
            breakdown_anonymization=[
                ScoreItemModel.of(i) for i in scores.breakdown_anonymization
            ],
            breakdown_security=[ScoreItemModel.of(i) for i in scores.breakdown_security],
        )


class WallpaperSlotInfo(BaseModel):
    """Per-kind wallpaper metadata exposed in the profile envelope."""

    has: bool
    fit_mode: str = "contain"
    uploaded_at: datetime | None = None


class ProfileEnvelope(BaseModel):
    """Profile + storage metadata (source, timestamps, active flag) + scores."""

    profile: Profile
    source: Literal["template", "user"]
    is_active: bool = False
    scores: ProfileScoresModel
    created_at: datetime
    updated_at: datetime
    # Per-kind wallpaper metadata. The UI renders one upload slot per kind
    # and uses `uploaded_at` as cache-buster on the image URL.
    wallpapers: dict[str, WallpaperSlotInfo] = Field(default_factory=dict)

    @classmethod
    def of(
        cls,
        stored: StoredProfile,
        *,
        active_name: str | None,
        wifi_secrets: dict[str, tuple[str, str]] | None = None,
        wallpapers: dict[tuple[str, str], "WallpaperRecord"] | None = None,
    ) -> ProfileEnvelope:
        scores = compute_scores(
            stored.profile,
            wifi_secrets=wifi_secrets,  # type: ignore[arg-type]
        )
        # Build the per-kind block. Missing kinds get `has: false` so the
        # UI doesn't have to defensively check for undefined entries.
        slots: dict[str, WallpaperSlotInfo] = {}
        for kind in KINDS:
            wp = (wallpapers or {}).get((stored.profile.name, kind))
            slots[kind] = WallpaperSlotInfo(
                has=wp is not None,
                fit_mode=wp.fit_mode if wp else "contain",
                uploaded_at=wp.uploaded_at if wp else None,
            )
        return cls(
            profile=stored.profile,
            source=stored.source,
            is_active=(active_name == stored.profile.name),
            scores=ProfileScoresModel.of(scores),
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            wallpapers=slots,
        )


async def _gather_wifi_secrets(wifi: WifiSsidStore) -> dict[str, tuple[str, str]]:
    """Return slug → (security, decrypted_password) for every SSID in the catalog."""
    from app.vpn.crypto import VPNCryptoError

    ssids = await wifi.list_all()
    out: dict[str, tuple[str, str]] = {}
    for ssid in ssids:
        try:
            pw = await wifi.get_password(ssid.slug)
        except VPNCryptoError:
            pw = ""
        out[ssid.slug] = (ssid.security, pw)
    return out


class ActiveProfileResponse(BaseModel):
    active_name: str | None
    profile: Profile | None
    # Per-subsystem application report (None when /active is just queried,
    # populated on /activate). Each entry: { ok, skipped, changes, errors }.
    applied: dict[str, dict] | None = None


class DuplicateRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)


# ---------------------------- endpoints ---------------------------- #


def _wallpaper_store(request: Request) -> WallpaperStore:
    sf = make_session_factory(request.app.state.db_engine)
    return WallpaperStore(sf)


@router.get("", response_model=list[ProfileEnvelope])
async def list_profiles(
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[ProfileEnvelope]:
    active = await store.get_active_name()
    items = await store.list_all()
    secrets = await _gather_wifi_secrets(wifi)
    # Keys are (profile_name, kind) — ProfileEnvelope.of indexes by both.
    wallpapers = await _wallpaper_store(request).list_existing()
    return [
        ProfileEnvelope.of(
            s, active_name=active, wifi_secrets=secrets, wallpapers=wallpapers,
        )
        for s in items
    ]


@router.get("/active", response_model=ActiveProfileResponse)
async def get_active_profile(
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ActiveProfileResponse:
    active = await store.get_active_name()
    if active is None:
        return ActiveProfileResponse(active_name=None, profile=None)
    try:
        stored = await store.get(active)
    except ProfileNotFoundError:
        # Active pointer was stale — clear it.
        return ActiveProfileResponse(active_name=None, profile=None)
    return ActiveProfileResponse(active_name=active, profile=stored.profile)


@router.get("/{name}", response_model=ProfileEnvelope)
async def get_profile(
    name: str,
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProfileEnvelope:
    try:
        stored = await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    active = await store.get_active_name()
    secrets = await _gather_wifi_secrets(wifi)
    ws = _wallpaper_store(request)
    wallpapers: dict[tuple[str, str], WallpaperRecord] = {}
    for kind in KINDS:
        wp = await ws.get_meta(name, kind=kind)
        if wp:
            wallpapers[(name, kind)] = wp
    return ProfileEnvelope.of(
        stored, active_name=active, wifi_secrets=secrets, wallpapers=wallpapers,
    )


@router.post(
    "",
    response_model=ProfileEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    profile: Profile,
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProfileEnvelope:
    try:
        stored = await store.create(profile, source="user")
    except ProfileDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"profile {str(exc)!r} already exists",
        ) from exc
    except ProfileStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    # Seed cyber defaults so the new profile already has wallpapers visible
    # in the UI. User can override per-slot via uploads.
    from app.profiles.default_wallpaper import seed_default_wallpapers_if_missing
    try:
        await seed_default_wallpapers_if_missing(
            stored.profile, _wallpaper_store(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "profile.wallpaper.seed_default_failed",
            profile=stored.profile.name, error=str(exc),
        )
    active = await store.get_active_name()
    logger.info("profile.created", name=stored.profile.name)
    return ProfileEnvelope.of(stored, active_name=active)


@router.put("/{name}", response_model=ProfileEnvelope)
async def update_profile(
    name: str,
    profile: Profile,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProfileEnvelope:
    try:
        stored = await store.update(name, profile)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    active = await store.get_active_name()
    logger.info("profile.updated", name=name)
    return ProfileEnvelope.of(stored, active_name=active)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    name: str,
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await store.delete(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    except ProfileImmutableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    # Cascade wallpaper cleanup. Failure here is logged but not fatal — the
    # profile is gone and a dangling wallpaper is at worst a few orphan KB.
    try:
        await _wallpaper_store(request).delete_all(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile.wallpaper_cascade_failed", name=name, error=str(exc))
    logger.info("profile.deleted", name=name)


# ---- wallpaper endpoints --------------------------------------------------

def _ensure_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"kind must be one of {list(KINDS)}, got {kind!r}",
        )
    return kind


@router.get("/{name}/wallpaper/{kind}")
async def get_wallpaper_kind(
    name: str,
    kind: str,
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Serve the wallpaper bytes for {kind} (home or lock)."""
    _ensure_kind(kind)
    blob = await _wallpaper_store(request).get_blob(name, kind=kind)
    if blob is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {kind} wallpaper set for this profile",
        )
    return Response(
        content=blob.content,
        media_type=blob.mime_type,
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@router.put("/{name}/wallpaper/{kind}")
async def upload_wallpaper_kind(
    name: str,
    kind: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    user: Annotated[User, Depends(get_current_user)],
    fit_mode: str = "contain",
) -> dict:
    """Upload a wallpaper for {kind} with the given fit_mode."""
    _ensure_kind(kind)
    if fit_mode not in FIT_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"fit_mode must be one of {list(FIT_MODES)}, got {fit_mode!r}",
        )
    try:
        await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file too large: {len(content)} > {MAX_BYTES}",
        )
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported content-type {mime!r}; allowed: {list(ALLOWED_MIME)}",
        )
    try:
        meta = await _wallpaper_store(request).upsert(
            name, content, mime, kind=kind, fit_mode=fit_mode,
        )
    except WallpaperError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    logger.info(
        "profile.wallpaper.uploaded",
        username=user.username, name=name, kind=kind,
        fit_mode=fit_mode, mime=mime, size=meta.size_bytes,
    )
    return {
        "profile_name": meta.profile_name,
        "kind": meta.kind,
        "fit_mode": meta.fit_mode,
        "mime_type": meta.mime_type,
        "size_bytes": meta.size_bytes,
        "uploaded_at": meta.uploaded_at.isoformat(),
    }


@router.delete("/{name}/wallpaper/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wallpaper_kind(
    name: str,
    kind: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    _ensure_kind(kind)
    ok = await _wallpaper_store(request).delete(name, kind=kind)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {kind} wallpaper to delete",
        )
    logger.info(
        "profile.wallpaper.deleted",
        username=user.username, name=name, kind=kind,
    )


@router.get("/{name}/wallpaper-studio/preview/{kind}")
async def wallpaper_studio_preview(
    name: str,
    kind: str,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Render a clean wallpaper for the profile using the controller cyber theme.

    Returns the PNG bytes without saving. Use to preview before applying.
    """
    _ensure_kind(kind)
    try:
        stored = await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    from app.profiles.wallpaper_studio import render_wallpaper
    png = await render_wallpaper(stored.profile, kind, ssh)  # type: ignore[arg-type]
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{name}/wallpaper-studio/apply/{kind}")
async def wallpaper_studio_apply(
    name: str,
    kind: str,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Generate the cyber-themed wallpaper AND save it as the profile's slot."""
    _ensure_kind(kind)
    try:
        stored = await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    from app.profiles.wallpaper_studio import render_wallpaper
    png = await render_wallpaper(stored.profile, kind, ssh)  # type: ignore[arg-type]
    meta = await _wallpaper_store(request).upsert(
        name, png, "image/png", kind=kind, fit_mode="cover",
    )
    logger.info(
        "profile.wallpaper_studio.applied",
        username=user.username, name=name, kind=kind, size=meta.size_bytes,
    )
    # Return the full metadata shape (matches ProfileWallpaperMeta TS type).
    return {
        "profile_name": meta.profile_name,
        "kind": meta.kind,
        "fit_mode": meta.fit_mode,
        "mime_type": meta.mime_type,
        "size_bytes": meta.size_bytes,
        "uploaded_at": meta.uploaded_at.isoformat(),
    }


@router.post("/wallpapers/regenerate-all")
async def regenerate_all_wallpapers(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Regenerate cyber-theme wallpapers for every profile × both kinds AND
    push the active profile's wallpapers to the Slate's filesystem.

    Renders via wallpaper_studio (the live cyber theme) and upserts every
    (profile, kind) slot in the DB. Existing user uploads ARE overwritten —
    if you want to preserve a custom upload, don't run this.

    After the DB regen we also push the ACTIVE profile's wallpapers to
    `/etc/gl_screen/wallpaper_{home,wake_display}.png` and restart gl_screen
    so the change is visible on the panel without needing a manual activation.
    """
    from app.profiles.screen_applier import apply_screen_wallpaper
    from app.profiles.wallpaper_studio import render_wallpaper

    items = await store.list_all()
    ws = _wallpaper_store(request)
    results: list[dict] = []
    errors: list[dict] = []
    for stored in items:
        for kind in ("home", "lock"):
            try:
                png = await render_wallpaper(stored.profile, kind, ssh)  # type: ignore[arg-type]
                meta = await ws.upsert(
                    stored.profile.name, png, "image/png",
                    kind=kind, fit_mode="cover",
                )
                results.append({
                    "profile_name": stored.profile.name,
                    "kind": kind,
                    "size_bytes": meta.size_bytes,
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "profile_name": stored.profile.name,
                    "kind": kind,
                    "error": f"{type(exc).__name__}: {exc}",
                })

    # Push the active profile's freshly-regenerated wallpapers to the Slate.
    # Without this step the user sees "success" but the panel still shows
    # the old PNGs sitting in /etc/gl_screen/. Reuses the same applier as
    # profile activation — single gl_screen restart at the end.
    pushed: dict | None = None
    active_name = await store.get_active_name()
    if active_name:
        try:
            active = await store.get(active_name)
            push_report = await apply_screen_wallpaper(active.profile, ssh, ws)
            pushed = {
                "profile_name": active_name,
                **push_report.to_dict(),
            }
        except Exception as exc:  # noqa: BLE001
            pushed = {
                "profile_name": active_name,
                "ok": False,
                "skipped": False,
                "changes": [],
                "errors": [f"push failed: {type(exc).__name__}: {exc}"],
            }

    logger.info(
        "profile.wallpapers.regenerate_all",
        username=user.username,
        regenerated=len(results), errors=len(errors),
        pushed_to=active_name,
    )
    return {
        "regenerated": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
        "pushed_active": pushed,
    }


@router.post("/{name}/wallpaper/seed-defaults")
async def regenerate_default_wallpapers(
    name: str,
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """(Re)generate cyber default wallpapers for slots that don't have a
    user-uploaded one. Use after changing a profile's name or color so the
    procedural images match the new look. User uploads are preserved."""
    try:
        stored = await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    # Force-regenerate by deleting any default rows first? No — we leave
    # user uploads alone. The seed helper only fills empty slots.
    # If the user wants to RESET to a default after having uploaded, they
    # should delete the slot via DELETE /wallpaper/{kind} first.
    from app.profiles.default_wallpaper import seed_default_wallpapers_if_missing
    seeded = await seed_default_wallpapers_if_missing(
        stored.profile, _wallpaper_store(request),
    )
    logger.info(
        "profile.wallpaper.seed_defaults",
        username=user.username, name=name, seeded=seeded,
    )
    return {"seeded": seeded}


@router.get("/{name}/activate-qr")
async def activate_qr(
    name: str,
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Return an SVG QR code encoding the URL that opens this profile in the UI.

    The encoded URL is `<origin>/profiles?activate=<name>`. The frontend
    intercepts that query param and triggers activation. Useful with the
    per-profile wallpaper feature: print or display the QR + wallpaper as
    a physical "tap to switch" card.
    """
    try:
        await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc

    # Origin discovery: prefer the explicit `Origin` header (set by the
    # frontend's axios), fall back to Referer (browser), then localhost.
    origin = (
        request.headers.get("origin")
        or (request.headers.get("referer") or "").rstrip("/").split("/api")[0]
        or "http://localhost:5173"
    )
    target = f"{origin}/profiles?activate={name}"

    import qrcode
    import qrcode.image.svg
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(target)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    svg_bytes = img.to_string()
    return Response(
        content=svg_bytes,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post(
    "/{name}/duplicate",
    response_model=ProfileEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_profile(
    name: str,
    body: DuplicateRequest,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ProfileEnvelope:
    try:
        stored = await store.duplicate(name, body.new_name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    except ProfileDuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"profile {str(exc)!r} already exists",
        ) from exc
    except ProfileStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    active = await store.get_active_name()
    logger.info("profile.duplicated", source=name, new=stored.profile.name)
    return ProfileEnvelope.of(stored, active_name=active)


@router.post("/{name}/activate", response_model=ActiveProfileResponse)
async def activate_profile(
    name: str,
    request: Request,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ActiveProfileResponse:
    """Mark a profile as active AND push the appliable subsystems to the Slate.

    Currently wired: Tailscale subsystem (Phase 2b — Tailscale slice). Other
    subsystems (Wi-Fi SSIDs, AdGuard, firewall lockdown, VPN switch) are
    still marker-only and will be progressively wired in.
    """
    try:
        await store.set_active(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    stored = await store.get(name)

    # Apply pipeline:
    #   1. Push "MISE A JOUR depuis Slate Controller" overlay so the user
    #      sees the change in progress on the physical screen.
    #   2. Run Tailscale applier (~1-3s of SSH work).
    #   3. Run screen wallpaper applier — overwrites the overlay with the
    #      profile's actual wallpaper(s) at the end.
    # Errors at any step don't roll back the active marker; the report
    # tells the user which subsystems didn't land.
    from app.profiles.screen_applier import apply_screen_wallpaper
    from app.profiles.status_screen import push_status_overlay
    from app.tailscale.applier import apply_tailscale_profile
    from app.tailscale.client import TailscaleClient

    applied: dict[str, dict] = {}

    # 1. Status message via the public display_message() helper. Gated by
    # the Settings → Communication → "show_screen_messages" toggle so the
    # user can opt out of the visible screen takeover during activations.
    ws = _wallpaper_store(request)
    from app.db.database import make_session_factory as _msf
    from app.settings.slate_comms import SlateCommsStore as _SCS
    comms_store = _SCS(_msf(request.app.state.db_engine))
    comms = await comms_store.get()
    import asyncio as _asyncio
    _fb_task: _asyncio.Task | None = None
    if comms.get("show_screen_messages", True):
        # restart_after=False because step 3 below (screen_applier) will do
        # the single final restart after writing the new wallpapers — single
        # restart = no flicker.
        # 6s hold (was 4s) so the message is comfortable to spot — the user
        # is typically looking at the browser when clicking Activer and the
        # first 1-2s are spent SSH-connecting + writing fb.
        from app.profiles.slate_message import display_message
        _fb_task = _asyncio.create_task(
            display_message(
                ssh,
                title=f"loading profile {name}",
                subtitle="from slate-controller",
                target=name,
                duration_seconds=6.0,
                restart_after=False,
            )
        )

    # 2. Tailscale.
    ts_report = await apply_tailscale_profile(
        stored.profile.tailscale,
        TailscaleClient(ssh),
        request.app.state.tailscale_ha_store,
    )
    applied["tailscale"] = ts_report.to_dict()

    # 3. Wait for the fb takeover to finish (it includes the on-screen hold
    # + gl_screen restart). Then push the profile's wallpapers so gl_screen
    # repaints with the right content.
    if _fb_task is not None:
        try:
            fb_report = await _fb_task
            applied["status_overlay"] = fb_report.to_dict()
        except Exception as exc:  # noqa: BLE001
            applied["status_overlay"] = {
                "ok": False, "steps": [], "errors": [f"fb takeover crashed: {exc}"],
            }
    else:
        applied["status_overlay"] = {
            "ok": True, "steps": ["disabled by Settings.show_screen_messages=false"],
            "errors": [],
        }
    try:
        screen_report = await apply_screen_wallpaper(stored.profile, ssh, ws)
        applied["screen"] = screen_report.to_dict()
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb
        logger.error(
            "profile.screen_apply.crashed",
            name=name, error=str(exc), tb=_tb.format_exc(),
        )
        applied["screen"] = {
            "ok": False, "skipped": False, "changes": [],
            "errors": [f"applier crashed: {type(exc).__name__}: {exc}"],
        }

    logger.info(
        "profile.activated",
        name=name,
        applied_to_slate=True,
        tailscale_ok=ts_report.ok,
        tailscale_changes=ts_report.changes,
        tailscale_errors=ts_report.errors,
        screen_ok=screen_report.ok,
        screen_changes=screen_report.changes,
        screen_errors=screen_report.errors,
    )
    return ActiveProfileResponse(active_name=name, profile=stored.profile, applied=applied)


@router.post("/{name}/plan")
async def plan_profile_activation(
    name: str,
    store: Annotated[ProfileStore, Depends(get_profile_store)],
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    vpn_cfg: Annotated[VPNConfigStore, Depends(get_vpn_config_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Dry-run: return the list of ops we WOULD perform to materialize this profile.

    No call to the Slate is made. The response is consumed by the UI so the
    user can review every subsystem (VPN, DNS, firewall, Wi-Fi, AdGuard, Tor,
    Tailscale, logging) before we flip on real execution in Phase 2b.
    """
    try:
        stored = await store.get(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    applier = ProfileApplier(wifi_store=wifi, vpn_config_store=vpn_cfg)
    plan = await applier.plan(stored.profile)
    logger.info(
        "profile.plan",
        name=name,
        steps=plan.step_count,
        blockers=len(plan.blockers),
    )
    return plan.to_dict()
