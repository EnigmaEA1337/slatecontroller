"""Push a profile's wallpaper to the Slate's front touchscreen.

How it works:
  1. Read the wallpaper bytes from the DB-backed WallpaperStore.
  2. Resize/letterbox to the screen's native 320×240 RGB (no alpha).
  3. Atomically replace /etc/gl_screen/wallpaper_home.png via SSH.
  4. Tell gl_screen to reload via `ubus call gl_screen set '{"method":"reload"}'`.

Notes:
  - We use base64 + `printf | base64 -d > /tmp/x; mv /tmp/x /target` to push
    binary content over the existing SSH channel without needing SFTP.
  - The image is RGB without alpha (matching the OEM wallpaper format).
    Alpha-channel PNGs get flattened on a dark background to keep the
    contrast similar to GL.iNet's default.
  - Resize uses `ImageOps.fit` with center-crop — preserves the visual
    focus of arbitrary aspect ratios. Letterboxing was rejected because
    320×240 is small and black bars are ugly.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import structlog

from app.models.profile import Profile
from app.profiles.wallpaper_studio import render_wallpaper
from app.profiles.wallpapers import WallpaperStore
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

SCREEN_W = 320
SCREEN_H = 240
# Each kind maps to a *list* of target paths on the Slate FS — gl_screen
# resolves the actual wallpaper through both the legacy `/etc/gl_screen/`
# paths AND style-keyed copies under `/etc/gl_screen/image/`. The active
# style is governed by `WAKE_DISPLAY_STYLE` in the live config (currently
# style2 on the Slate 7 Pro). To be robust against config drift we write
# to every reasonable path — same bytes, idempotent. Verified via
# `strings /usr/bin/gl_screen | grep wallpaper`.
KIND_TO_PATHS: dict[str, list[str]] = {
    "home": [
        "/etc/gl_screen/wallpaper_home.png",
        "/etc/gl_screen/image/wallpaper.png",
        "/etc/gl_screen/image/wallpaper_home_style_default.png",
    ],
    "lock": [
        "/etc/gl_screen/wallpaper_wake_display.png",
        "/etc/gl_screen/image/wallpaper_wake_display_style1.png",
        "/etc/gl_screen/image/wallpaper_wake_display_style2.png",
    ],
}
DARK_FILL = (10, 10, 20)  # letterbox/pillarbox fill colour


@dataclass
class ScreenApplyReport:
    skipped: bool = False
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "changes": self.changes,
            "errors": self.errors,
        }


def _resize_to_screen(raw: bytes, fit_mode: str = "contain") -> bytes:
    """Resize arbitrary image to 320×240 RGB PNG using `fit_mode`.

    Modes:
      - contain : letterbox/pillarbox onto a dark canvas — no crop, no
                  distortion. Whole image visible, surplus filled in dark.
                  Recommended for arbitrary aspect ratios.
      - cover   : center-crop to fully fill the canvas. No margins but
                  edges of the source get clipped.
      - stretch : non-uniform scale to fit. Typically ugly but predictable.
    """
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(raw))
    # Flatten any alpha onto the dark fill.
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, DARK_FILL)
        bg.paste(img, mask=img.convert("RGBA").split()[-1])
        img = bg
    else:
        img = img.convert("RGB")

    target = (SCREEN_W, SCREEN_H)
    if fit_mode == "cover":
        result = ImageOps.fit(img, target, Image.Resampling.LANCZOS)
    elif fit_mode == "stretch":
        result = img.resize(target, Image.Resampling.LANCZOS)
    else:
        # contain (default)
        contained = ImageOps.contain(img, target, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", target, DARK_FILL)
        # Center the contained image on the canvas.
        x = (SCREEN_W - contained.width) // 2
        y = (SCREEN_H - contained.height) // 2
        canvas.paste(contained, (x, y))
        result = canvas

    out = io.BytesIO()
    result.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def _push_file(ssh: SlateSSH, payload: bytes, target: str) -> None:
    """Atomically replace `target` with `payload`.

    See SlateSSH.put_bytes — uses stdin-piped `cat > file` because dropbear
    on this Slate doesn't ship the SFTP subsystem.
    """
    await ssh.put_bytes(payload, target)


async def apply_screen_wallpaper(
    profile: Profile,
    ssh: SlateSSH,
    wallpaper_store: WallpaperStore,
) -> ScreenApplyReport:
    """Push the active profile's wallpapers (home + lock) to the Slate.

    If the user uploaded a custom wallpaper for a kind, we use it. Otherwise
    we generate a procedural cyberpunk default keyed on the profile's name
    and color so the screen always reflects the active profile — never the
    OEM GL.iNet default. A single gl_screen daemon restart at the end picks
    up both files.
    """
    rep = ScreenApplyReport()

    pushed_any = False
    for kind, target_paths in KIND_TO_PATHS.items():
        blob = await wallpaper_store.get_blob(profile.name, kind=kind)

        # 1. Source the image bytes: custom upload OR procedural default.
        if blob is not None:
            try:
                resized = _resize_to_screen(blob.content, fit_mode=blob.fit_mode)
            except Exception as exc:  # noqa: BLE001
                rep.errors.append(f"[{kind}] resize failed: {exc}")
                continue
            rep.changes.append(
                f"[{kind}] custom resized {len(blob.content)} → {len(resized)} bytes "
                f"(fit_mode={blob.fit_mode})"
            )
        else:
            try:
                # wallpaper_studio renders the proper cyber-theme PNG
                # (240×320 portrait) using the OEM TTF. Then we resize
                # to the screen's landscape 320×240 with cover so it
                # fills the framebuffer cleanly.
                studio_png = await render_wallpaper(profile, kind, ssh)  # type: ignore[arg-type]
                resized = _resize_to_screen(studio_png, fit_mode="cover")
            except Exception as exc:  # noqa: BLE001
                rep.errors.append(f"[{kind}] studio render failed: {exc}")
                continue
            rep.changes.append(
                f"[{kind}] studio cyber fallback ({len(resized)} bytes)"
            )

        # 2. Push to EVERY known path for this kind. gl_screen's resolution
        # depends on WAKE_DISPLAY_STYLE (and possibly other knobs) — writing
        # to all paths means we hit the active one regardless of config.
        kind_ok = False
        for target_path in target_paths:
            try:
                await _push_file(ssh, resized, target_path)
                rep.changes.append(f"[{kind}] wrote {target_path}")
                kind_ok = True
            except SlateSSHError as exc:
                rep.errors.append(f"[{kind}] upload {target_path}: {exc}")
        if kind_ok:
            pushed_any = True

    if not pushed_any:
        rep.skipped = True
        return rep

    # 2b. Ensure gl_screen uses the DISTINCT lock wallpaper. Default factory
    # config has `WALLPAPER_PAIR=1` which means "home and lock share the same
    # wallpaper" — gl_screen then uses /etc/gl_screen/wallpaper_home.png for
    # BOTH states, ignoring our /etc/gl_screen/wallpaper_wake_display.png.
    # Setting WALLPAPER_PAIR=0 makes it use the wake_display file for the
    # lock state (which is what we push the locked-terminal PNG to).
    # Idempotent — sets-and-commits-and-reloads even when already 0.
    try:
        cur = await ssh.run("uci get gl_screen.generic.WALLPAPER_PAIR 2>/dev/null", timeout=5)
        current_pair = cur.stdout.strip()
        if current_pair != "0":
            r = await ssh.run(
                "uci set gl_screen.generic.WALLPAPER_PAIR=0 && "
                "uci commit gl_screen && /etc/init.d/gl_screen reload",
                timeout=15,
            )
            if r.exit_status == 0:
                rep.changes.append(
                    f"WALLPAPER_PAIR {current_pair or '?'} → 0 (lock uses distinct wallpaper)"
                )
            else:
                rep.errors.append(f"uci WALLPAPER_PAIR set failed (exit={r.exit_status})")
    except SlateSSHError as exc:
        rep.errors.append(f"uci WALLPAPER_PAIR config: {exc}")

    # 3. Single daemon restart at the end — picks up all kinds at once.
    try:
        r = await ssh.run("/etc/init.d/gl_screen restart 2>&1", timeout=15)
        if r.exit_status != 0:
            rep.errors.append(f"gl_screen restart failed: {r.stdout[:200]}")
        else:
            rep.changes.append("gl_screen daemon restarted (cache cleared)")
    except SlateSSHError as exc:
        rep.errors.append(f"gl_screen restart SSH error: {exc}")

    logger.info(
        "profile.screen_wallpaper_applied",
        profile=profile.name,
        ok=rep.ok,
    )
    return rep
