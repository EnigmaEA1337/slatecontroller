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
    wifi: Annotated[WifiSsidStore, Depends(get_wifi_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ActiveProfileResponse:
    """Mark a profile as active AND apply it on the Slate.

    Two paths, picked at runtime based on agent presence :

    **Agent path** (preferred — when `slate-ctrl version` succeeds on the
    Slate). The controller :
      1. Syncs the profile JSON + loading screen + wallpapers to the
         Slate so the agent's local handlers have fresh artifacts.
      2. Invokes `slate-ctrl apply <name>` over SSH — the agent's 9 shell
         handlers (dns, wifi, adguard, tailscale, screen, firewall, vpn,
         tor, wallpaper) apply the profile *locally* on the Slate. Same
         path the physical button uses ; same path that re-applies the
         active profile at boot.
      3. Applies the controller-side HA watchdog overrides (cf
         `apply_tailscale_ha_only`) — the agent doesn't touch the
         controller's DB.

    **Fallback path** (agent not deployed). The controller drives each
    subsystem itself over SSH : status overlay → Tailscale via
    `TailscaleClient` → screen wallpaper applier. This is the original
    Phase 2b implementation, kept so the "Activer" button degrades
    gracefully on freshly-adopted devices before `/api/agent/deploy`
    has been run.

    Errors at any step don't roll back the active marker; `applied`
    surfaces which subsystems landed.
    """
    try:
        await store.set_active(name)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {name!r} not found",
        ) from exc
    stored = await store.get(name)

    # Path selection: is slate-ctrl installed AND callable?
    from app.slate_agent.deploy import get_agent_version

    agent_version = await get_agent_version(ssh)
    applied: dict[str, dict] = {}

    if agent_version is not None:
        applied = await _activate_via_agent(
            name=name, stored=stored, request=request, ssh=ssh, wifi=wifi,
        )
        logger.info(
            "profile.activated.via_agent",
            name=name, agent_version=agent_version,
            agent_ok=applied.get("agent", {}).get("ok"),
        )
    else:
        applied = await _activate_via_controller(
            name=name, stored=stored, request=request, ssh=ssh,
        )
        logger.info(
            "profile.activated.via_controller_fallback",
            name=name,
        )

    return ActiveProfileResponse(active_name=name, profile=stored.profile, applied=applied)


async def _activate_via_agent(
    *,
    name: str,
    stored: StoredProfile,
    request: Request,
    ssh: SlateSSH,
    wifi: WifiSsidStore,
) -> dict[str, dict]:
    """Agent-driven activation. See `activate_profile` docstring."""
    from app.slate_agent.sync import (
        apply_remote_profile,
        sync_loading_screens,
        sync_profile_wallpapers,
        sync_profiles,
    )
    from app.tailscale.applier import apply_tailscale_ha_only

    applied: dict[str, dict] = {}
    profile = stored.profile
    wifi_catalog = await wifi.list_all()
    wallpaper_store = _wallpaper_store(request)

    # 1. Sync the 3 artifact families for this single profile. Same code
    # the user runs from "Synchroniser le Slate" — we just scope it to
    # the one profile being activated so the apply works against
    # up-to-date artifacts. ~1-3s of SSH/SFTP work.
    json_rep = await sync_profiles(
        ssh, [profile], wifi_catalog=wifi_catalog,
        wallpaper_store=wallpaper_store,
    )
    screen_rep = await sync_loading_screens(ssh, [profile])
    wallpaper_rep = await sync_profile_wallpapers(
        ssh, [profile], wallpaper_store,
    )
    applied["sync"] = {
        "json": json_rep.to_dict(),
        "screens": screen_rep.to_dict(),
        "wallpapers": wallpaper_rep.to_dict(),
    }

    # 2. Hand the actual apply to the agent. Its handlers paint the
    # loading screen, run each subsystem (dns/wifi/adguard/…) sequentially,
    # then paint the final wallpaper. Timeout is high (60s) because the
    # firewall reload alone can take 5-10s.
    ok, output = await apply_remote_profile(ssh, name)
    applied["agent"] = {"ok": ok, "output": output}

    # 3. Controller-side bit the agent can't do : HA watchdog config in
    # our own DB. Returns a TailscaleApplyReport even if profile.tailscale.ha
    # is None (no-op then).
    ha_rep = await apply_tailscale_ha_only(
        profile.tailscale, request.app.state.tailscale_ha_store,
    )
    applied["ha_watchdog"] = ha_rep.to_dict()

    return applied


async def _activate_via_controller(
    *,
    name: str,
    stored: StoredProfile,
    request: Request,
    ssh: SlateSSH,
) -> dict[str, dict]:
    """Legacy fallback when the agent isn't deployed. Same flow as the
    pre-agent activation: overlay → Tailscale via SSH → screen applier."""
    from app.profiles.screen_applier import apply_screen_wallpaper
    from app.profiles.slate_message import display_message
    from app.tailscale.applier import apply_tailscale_profile
    from app.tailscale.client import TailscaleClient

    applied: dict[str, dict] = {}
    ws = _wallpaper_store(request)

    # 1. Status overlay — gated by show_screen_messages.
    from app.settings.slate_comms import SlateCommsStore as _SCS
    comms_store = _SCS(make_session_factory(request.app.state.db_engine))
    comms = await comms_store.get()
    import asyncio as _asyncio
    _fb_task: _asyncio.Task | None = None
    if comms.get("show_screen_messages", True):
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

    # 2. Tailscale via controller-side applier.
    ts_report = await apply_tailscale_profile(
        stored.profile.tailscale,
        TailscaleClient(ssh),
        request.app.state.tailscale_ha_store,
    )
    applied["tailscale"] = ts_report.to_dict()

    # 3. Wait for overlay; push wallpapers.
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
    return applied


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
