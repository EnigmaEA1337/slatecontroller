"""Sync profile definitions controller → Slate.

Serializes Pydantic `Profile` models to JSON and pushes them to
`/etc/slate-controller/profiles/<name>.json` on the Slate. The agent's
slate-ctrl picks them up at apply time. Idempotent — running sync again
overwrites the JSON with whatever the controller currently holds.

The JSON shape is just `Profile.model_dump()` — same schema the controller
uses internally. The shell handlers parse it with jsonfilter (OpenWrt's
JSON tool).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.adguard.feeds import get_feed
from app.models.profile import Profile
from app.profiles.wallpapers import WallpaperStore
from app.slate.ssh import SlateSSH, SlateSSHError
from app.slate_agent.deploy import REMOTE_PROFILES_DIR, REMOTE_SCREENS_DIR
from app.wifi.models import WifiSsidPublic

# Where pre-rendered wallpaper PNGs live on the Slate (one per profile×kind).
# The wallpaper.sh handler copies the matching file into the gl_screen paths
# at apply time. Same layout idea as screens/loading_<name>.raw.
REMOTE_WALLPAPERS_DIR = "/etc/slate-controller/wallpapers"

logger = structlog.get_logger(__name__)


def _profile_to_agent_payload(
    profile: Profile, wifi_catalog: list[WifiSsidPublic],
) -> dict[str, Any]:
    """Transform a Pydantic Profile into the on-Slate JSON shape.

    The on-disk JSON is a *deployment artifact*, not a raw Pydantic dump.
    The handlers want flat, self-sufficient blocks (no need to consult a
    separate catalog), so we resolve cross-references here:

      - Wi-Fi: the Pydantic profile has `ssids: [{slug, enabled}]`. The
        handler needs the broadcast SSID name to find the uci section, so
        we hoist that under `wifi.ssids: [{slug, name, band, security,
        network_slug, enabled}]`. The original `ssids` field is preserved
        so we don't break introspection but the agent reads `wifi.*`.
      - AdGuard: the Pydantic profile has `adguard.lists: [slug, ...]`.
        The handler needs the upstream URL + a display name to call the
        local REST API, so we replace it with `adguard.lists: [{slug,
        name, url}]`. Slugs absent from the catalog get `{slug, missing:
        true}` so the handler can log them.
    """
    payload = profile.model_dump(mode="json")

    catalog_by_slug = {s.slug: s for s in wifi_catalog}
    resolved_ssids: list[dict[str, Any]] = []
    for ref in profile.ssids:
        catalog = catalog_by_slug.get(ref.slug)
        if catalog is None:
            # Reference to a non-existent SSID — surface in the JSON so the
            # handler can log it and the operator can clean up.
            resolved_ssids.append(
                {"slug": ref.slug, "enabled": ref.enabled, "missing": True}
            )
            continue
        resolved_ssids.append({
            "slug": ref.slug,
            "name": catalog.ssid_name,
            "band": catalog.band,
            "security": catalog.security,
            "network_slug": catalog.network_slug,
            "enabled": ref.enabled,
        })
    payload["wifi"] = {"ssids": resolved_ssids}

    # AdGuard: replace `lists: [slug, ...]` with enriched objects so the
    # shell handler doesn't need a catalog file on the Slate.
    adguard_block = payload.get("adguard") or {}
    resolved_lists: list[dict[str, Any]] = []
    for slug in profile.adguard.lists:
        feed = get_feed(slug)
        if feed is None:
            resolved_lists.append({"slug": slug, "missing": True})
            continue
        resolved_lists.append({
            "slug": feed.slug,
            "name": feed.name,
            "url": feed.url,
        })
    adguard_block["lists"] = resolved_lists
    payload["adguard"] = adguard_block
    return payload


def add_wallpaper_block(
    payload: dict[str, Any],
    wallpaper_kinds_present: set[str],
) -> dict[str, Any]:
    """Mutate `payload` in place to add a `wallpaper: {home, lock}` block.

    The block tells the wallpaper.sh handler which kind to copy from the
    pre-rendered cache on the Slate. Without it, the handler can't know
    which file to look for (a profile might have a `home` wallpaper but
    no `lock`, etc.). Empty values are still included so the handler
    knows to clear stale state.
    """
    payload["wallpaper"] = {
        "home": "home" in wallpaper_kinds_present,
        "lock": "lock" in wallpaper_kinds_present,
    }
    return payload


@dataclass
class SyncReport:
    pushed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "pushed": self.pushed, "errors": self.errors}


async def sync_profiles(
    ssh: SlateSSH, profiles: list[Profile],
    wifi_catalog: list[WifiSsidPublic] | None = None,
    wallpaper_store: WallpaperStore | None = None,
) -> SyncReport:
    """Push every profile's JSON to the Slate.

    `wifi_catalog` lets us resolve SSID slugs → broadcast names so the
    wifi.sh handler can find the uci section directly. Pass None if you
    don't care about Wi-Fi (handler will skip with a warning).

    `wallpaper_store` lets us add a `wallpaper: {home, lock}` block to each
    profile JSON indicating which wallpaper kinds exist for that profile.
    The wallpaper.sh handler reads that block to know which pre-rendered
    PNG to copy from /etc/slate-controller/wallpapers/. Pass None and
    every profile gets `wallpaper: {home: false, lock: false}` → the
    handler is a no-op.

    Existing JSONs are overwritten. Old profiles that are no longer present
    are NOT pruned — `slate-ctrl list` will still see them.
    """
    rep = SyncReport()
    catalog = wifi_catalog or []

    # Pull the wallpaper index ONCE rather than N queries (N profiles).
    wallpaper_index: dict[tuple[str, str], object] = {}
    if wallpaper_store is not None:
        try:
            wallpaper_index = await wallpaper_store.list_existing()  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.wallpaper_index_failed", error=str(exc))

    # Ensure the destination dir exists (deploy_agent already creates it,
    # but sync can be called independently).
    try:
        await ssh.run(f"mkdir -p {REMOTE_PROFILES_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir profiles dir: {exc}")
        return rep

    for profile in profiles:
        try:
            data = _profile_to_agent_payload(profile, catalog)
            kinds_present = {
                kind for (pname, kind) in wallpaper_index
                if pname == profile.name
            }
            add_wallpaper_block(data, kinds_present)
            payload = json.dumps(data, indent=2).encode()
            target = f"{REMOTE_PROFILES_DIR}/{profile.name}.json"
            await ssh.put_bytes(payload, target, mode=0o644)
            rep.pushed.append(f"{profile.name} ({len(payload)}B)")
        except (SlateSSHError, ValueError) as exc:
            rep.errors.append(f"sync {profile.name}: {exc}")

    logger.info(
        "slate_agent.sync",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


async def sync_loading_screens(ssh: SlateSSH, profiles: list[Profile]) -> SyncReport:
    """Pre-render a "loading profile X" status PNG per profile, convert to
    RGB565 raw, and push to /etc/slate-controller/screens/loading_<name>.raw
    on the Slate.

    Rendering happens controller-side (Pillow + Slate's TTF cached locally)
    so the agent's `screen.sh` handler can show the message via a plain
    `cat raw > /dev/fb0` — no fonts, no PIL, no per-frame cost on the Slate.

    Same shape as sync_profiles. Failures are per-profile.
    """
    from app.profiles.fb_takeover import _png_to_rgb565_portrait
    from app.profiles.status_screen import render_status_image

    rep = SyncReport()
    try:
        await ssh.run(f"mkdir -p {REMOTE_SCREENS_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir screens dir: {exc}")
        return rep

    for profile in profiles:
        try:
            png = await render_status_image(
                ssh,
                title=f"loading profile {profile.name}",
                subtitle="from slate-controller",
                target=profile.name,
                kind="status",
            )
            raw = _png_to_rgb565_portrait(png)
            target = f"{REMOTE_SCREENS_DIR}/loading_{profile.name}.raw"
            await ssh.put_bytes(raw, target, mode=0o644)
            rep.pushed.append(f"loading_{profile.name} ({len(raw)}B raw)")
        except (SlateSSHError, ValueError, OSError) as exc:
            rep.errors.append(f"sync screen {profile.name}: {exc}")

    logger.info(
        "slate_agent.sync_screens",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


async def sync_profile_wallpapers(
    ssh: SlateSSH,
    profiles: list[Profile],
    wallpaper_store: WallpaperStore,
) -> SyncReport:
    """Pre-render the home + lock wallpaper PNGs for every profile that has
    one, and push them to /etc/slate-controller/wallpapers/<profile>_<kind>.png.

    Why pre-render here instead of letting the agent do it on the Slate :
      - PIL/Pillow is not on the stock GL.iNet firmware (and pulling it via
        opkg would add ~15 MB).
      - Resize + alpha-flatten + LANCZOS happens in microseconds on the
        controller's CPU; on the Slate's MT7986A it would be seconds.
      - We can re-use the SAME `_resize_to_screen` helper the controller
        already uses for direct (non-agent) apply paths — single source
        of truth for the rendering rules.

    Returns per-profile push entries. Skips profiles with no wallpaper at
    all (the handler will clear gl_screen back to OEM in that case).
    """
    from app.profiles.screen_applier import _resize_to_screen

    rep = SyncReport()

    try:
        await ssh.run(f"mkdir -p {REMOTE_WALLPAPERS_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir wallpapers dir: {exc}")
        return rep

    for profile in profiles:
        for kind in ("home", "lock"):
            try:
                blob = await wallpaper_store.get_blob(profile.name, kind=kind)
            except Exception as exc:  # noqa: BLE001
                rep.errors.append(
                    f"read {profile.name}/{kind}: {exc}"
                )
                continue
            if blob is None:
                continue  # no custom wallpaper for this (profile, kind)
            try:
                resized_png = _resize_to_screen(blob.content, fit_mode=blob.fit_mode)
            except Exception as exc:  # noqa: BLE001
                rep.errors.append(
                    f"resize {profile.name}/{kind}: {exc}"
                )
                continue
            target = f"{REMOTE_WALLPAPERS_DIR}/{profile.name}_{kind}.png"
            try:
                await ssh.put_bytes(resized_png, target, mode=0o644)
                rep.pushed.append(
                    f"{profile.name}/{kind} ({len(resized_png)}B "
                    f"fit={blob.fit_mode})"
                )
            except SlateSSHError as exc:
                rep.errors.append(
                    f"push {profile.name}/{kind}: {exc}"
                )

    logger.info(
        "slate_agent.sync_wallpapers",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


REMOTE_MENUS_DIR = "/etc/slate-controller/menus"


async def sync_button_cycle(
    ssh: SlateSSH, steps: list,
) -> SyncReport:
    """Push the reset-button cycle list + pre-rendered menu frames.

    Two artifacts go to the Slate :
      1. `/etc/slate-controller/cycle.json` — the ordered step list,
         consumed by `cycle-profile.sh` at button-press time.
      2. `/etc/slate-controller/menus/cycle_<N>.raw` — one 153 600 B
         RGB565 frame per cursor position, painted on the panel while
         the user keeps pressing (select-then-commit UX).
         Stale frames from a longer previous cycle are pruned so the
         menus dir mirrors the current cycle exactly.

    Idempotent — overwrites everything each call. Empty `steps` writes
    `{"steps": []}` and prunes all menu frames so the agent has an
    explicit "cycle disabled" signal.
    """
    from app.profiles.cycle_menu_renderer import render_menu_frames
    from app.profiles.fb_takeover import _png_to_rgb565_portrait
    from app.settings.button_cycle import remote_path, to_agent_payload

    rep = SyncReport()

    # 1. cycle.json
    payload = to_agent_payload(steps)
    try:
        await ssh.put_bytes(payload, remote_path(), mode=0o644)
        rep.pushed.append(f"cycle.json ({len(steps)} steps, {len(payload)}B)")
    except SlateSSHError as exc:
        rep.errors.append(f"sync cycle.json: {exc}")
        # If we can't even write cycle.json, no point trying frames.
        return rep

    # 2. menu frames. Render every cursor position, convert to RGB565,
    # push. The rendering happens here (controller-side, Pillow) so the
    # agent has nothing to compute at button-press time — `cat raw > fb0`
    # and it's painted.
    try:
        await ssh.run(f"mkdir -p {REMOTE_MENUS_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir menus dir: {exc}")
        return rep

    try:
        png_frames = await asyncio.to_thread(render_menu_frames, steps)
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"render menu frames: {exc}")
        return rep

    for idx, png in enumerate(png_frames):
        try:
            raw = await asyncio.to_thread(_png_to_rgb565_portrait, png)
        except Exception as exc:  # noqa: BLE001
            rep.errors.append(f"rgb565 frame #{idx}: {exc}")
            continue
        target = f"{REMOTE_MENUS_DIR}/cycle_{idx}.raw"
        try:
            await ssh.put_bytes(raw, target, mode=0o644)
            rep.pushed.append(f"cycle_{idx}.raw ({len(raw)}B)")
        except SlateSSHError as exc:
            rep.errors.append(f"push frame #{idx}: {exc}")

    # 3. Prune stale frames from previous syncs. If the cycle used to
    # have 6 steps and now has 3, frames 3-5 are stale and would
    # mislead the agent if cycle.json is briefly inconsistent.
    try:
        await ssh.run(
            f"for f in {REMOTE_MENUS_DIR}/cycle_*.raw; do "
            f"  idx=$(basename \"$f\" .raw | sed s/cycle_//); "
            f"  case \"$idx\" in ''|*[!0-9]*) continue ;; esac; "
            f"  [ \"$idx\" -ge {len(steps)} ] && rm -f \"$f\"; "
            f"done 2>/dev/null || true",
            timeout=10,
        )
    except SlateSSHError as exc:
        rep.errors.append(f"prune stale menu frames: {exc}")

    logger.info(
        "slate_agent.sync_button_cycle",
        ok=rep.ok, steps=len(steps), frames=len(png_frames),
    )
    return rep


async def list_remote_profiles(ssh: SlateSSH) -> list[str]:
    """Return the profile names currently present on the Slate."""
    try:
        r = await ssh.run("/usr/local/bin/slate-ctrl list 2>/dev/null", timeout=5)
        if r.exit_status != 0:
            return []
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    except SlateSSHError:
        return []


async def get_active_remote_profile(ssh: SlateSSH) -> str | None:
    """Return the agent's active profile (state/active file), or None."""
    try:
        r = await ssh.run("/usr/local/bin/slate-ctrl status 2>/dev/null", timeout=5)
        if r.exit_status == 0:
            name = r.stdout.strip()
            return name or None
    except SlateSSHError:
        pass
    return None


async def apply_remote_profile(ssh: SlateSSH, name: str) -> tuple[bool, str]:
    """Invoke `slate-ctrl apply <name>` on the Slate.

    Returns (ok, output). On success, the agent's local handlers have
    applied the profile — this replaces (when used) the per-subsystem
    appliers the controller runs over SSH today.
    """
    try:
        r = await ssh.run(
            f"/usr/local/bin/slate-ctrl apply {name} 2>&1", timeout=60,
        )
        return r.exit_status == 0, r.stdout
    except SlateSSHError as exc:
        return False, f"SSH error: {exc}"
