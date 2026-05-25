"""Procedural cyberpunk wallpaper generator + seed helper.

Generates a stylised PNG for each (profile, kind) — used as both the
fallback at activate-time (when no custom upload exists for a slot) and
the initial value materialised in the DB on profile seed/create.

Materialising the generated PNG in the DB means the UI shows the default
wallpaper as the current image of each slot — users see what's on their
Slate without having to activate first. Custom uploads overwrite the
default row in the same `profile_wallpapers` table.

Color palette is hard-aligned with the controller UI (frontend/src/index.css):
  bg          #06060f
  bg-elev     #0a0a18 / #14142a
  border      #1d1d36
  fg          #dee0eb
  muted       #7a7d96
  red accent  #ff3a52  (default)
  ok green    #5ae8a8
  warn yellow #ffb547

The wallpaper uses the profile's own `color` field as the accent (so each
profile is visually identifiable), or falls back to the controller's red.

Two layouts:
  - home (nav screen) — discreet edges, accent strip top + name bottom, so
    GL.iNet's widget cards aren't fighting our text in the centre.
  - lock (wake screen) — big centered profile name, clean look.

Both use the same pixelated PIL-default-font + NEAREST upscale technique
as status_screen.py (no system font dep, on-brand retro look).
"""

from __future__ import annotations

import io
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from app.models.profile import Profile

WallpaperKind = Literal["home", "lock"]

SCREEN_W = 320
SCREEN_H = 240

# Controller palette (frontend/src/index.css).
BG = (6, 6, 15)
BG_2 = (10, 10, 24)
SURFACE = (14, 14, 31)
BORDER = (29, 29, 54)
BORDER_STRONG = (44, 44, 79)
FG = (222, 224, 235)
MUTED = (122, 125, 150)
DIM = (77, 79, 107)
SCAN = (12, 14, 24)

DEFAULT_ACCENT = (255, 58, 82)  # --color-cyber-accent


def _hex_to_rgb(h: str | None) -> tuple[int, int, int]:
    """Parse #RRGGBB into (r,g,b). Returns DEFAULT_ACCENT on any error."""
    if not h:
        return DEFAULT_ACCENT
    h = h.strip().lstrip("#")
    if len(h) != 6:
        return DEFAULT_ACCENT
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return DEFAULT_ACCENT


def _render_text(
    text: str, *, scale: int, color: tuple[int, int, int]
) -> Image.Image:
    """PIL default bitmap font, upscaled NEAREST. Retro pixelated cyber look."""
    font = ImageFont.load_default()
    bbox = font.getbbox(text)
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    pad = 1
    tile = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.text((pad - bbox[0], pad - bbox[1]), text, fill=color, font=font)
    if scale > 1:
        tile = tile.resize(
            (tile.width * scale, tile.height * scale), Image.Resampling.NEAREST
        )
    return tile


def _paste_at(canvas: Image.Image, tile: Image.Image, x: int, y: int) -> None:
    canvas.paste(tile, (x, y))


def _paste_centered_x(
    canvas: Image.Image, tile: Image.Image, y: int
) -> None:
    canvas.paste(tile, ((SCREEN_W - tile.width) // 2, y))


def _draw_scanlines(canvas: Image.Image) -> None:
    """Subtle horizontal scanlines every 3 px — adds the CRT vibe."""
    d = ImageDraw.Draw(canvas)
    for y in range(0, SCREEN_H, 3):
        d.line([(0, y), (SCREEN_W, y)], fill=SCAN)


def _draw_corner_brackets(
    canvas: Image.Image, color: tuple[int, int, int]
) -> None:
    """[ ] style L-shaped corner ticks — HUD aesthetic."""
    d = ImageDraw.Draw(canvas)
    L = 14  # leg length
    M = 8   # margin from screen edge
    # Top-left
    d.line([(M, M), (M + L, M)], fill=color, width=1)
    d.line([(M, M), (M, M + L)], fill=color, width=1)
    # Top-right
    d.line([(SCREEN_W - M, M), (SCREEN_W - M - L, M)], fill=color, width=1)
    d.line([(SCREEN_W - M, M), (SCREEN_W - M, M + L)], fill=color, width=1)
    # Bottom-left
    d.line([(M, SCREEN_H - M), (M + L, SCREEN_H - M)], fill=color, width=1)
    d.line([(M, SCREEN_H - M), (M, SCREEN_H - M - L)], fill=color, width=1)
    # Bottom-right
    d.line([(SCREEN_W - M, SCREEN_H - M), (SCREEN_W - M - L, SCREEN_H - M)], fill=color, width=1)
    d.line([(SCREEN_W - M, SCREEN_H - M), (SCREEN_W - M, SCREEN_H - M - L)], fill=color, width=1)


def _draw_grid(
    canvas: Image.Image, step: int = 16, color: tuple[int, int, int] = BORDER
) -> None:
    """Faint background grid — subtle texture, not the main visual."""
    d = ImageDraw.Draw(canvas)
    for x in range(0, SCREEN_W, step):
        d.line([(x, 0), (x, SCREEN_H)], fill=color)
    for y in range(0, SCREEN_H, step):
        d.line([(0, y), (SCREEN_W, y)], fill=color)


def _gradient_fill(
    canvas: Image.Image,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> None:
    """Smooth vertical gradient top→bottom on the whole canvas."""
    d = ImageDraw.Draw(canvas)
    for y in range(SCREEN_H):
        t = y / (SCREEN_H - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        d.line([(0, y), (SCREEN_W, y)], fill=(r, g, b))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], w: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * w),
        round(a[1] + (b[1] - a[1]) * w),
        round(a[2] + (b[2] - a[2]) * w),
    )


def generate_default_wallpaper(profile: Profile, kind: WallpaperKind) -> bytes:
    """Procedural PNG aligned with the controller cyber theme + profile color.

    Layouts:
      - home (déverrouillé): clean, minimal — soft gradient + single accent
        strip. The user lives on this screen with GL.iNet's widgets on top,
        so the background stays calm and non-distracting.
      - lock (verrouillé): full cyberpunk — scanlines, HUD brackets, big
        profile name in accent color. Shown briefly so it can be loud.
    """
    accent = _hex_to_rgb(profile.color)
    name_upper = profile.name.upper()
    canvas = Image.new("RGB", (SCREEN_W, SCREEN_H), BG)

    if kind == "home":
        # Soft vertical gradient with a hint of the profile accent — calm.
        top = _mix(BG, accent, 0.05)
        bot = _mix(BG_2, accent, 0.10)
        _gradient_fill(canvas, top, bot)
        # One thin accent strip at the bottom — barely there but signals
        # which profile is active without competing with the GL.iNet UI.
        d = ImageDraw.Draw(canvas)
        d.line(
            [(28, SCREEN_H - 18), (SCREEN_W - 28, SCREEN_H - 18)],
            fill=accent, width=1,
        )
        # Tiny profile codename bottom-left, dimmed.
        tag = _render_text(name_upper, scale=2, color=_mix(MUTED, accent, 0.4))
        _paste_at(canvas, tag, 28, SCREEN_H - tag.height - 22 - 6)
    else:
        # LOCK screen: full HUD treatment.
        _draw_grid(canvas, step=20, color=BG_2)
        _draw_scanlines(canvas)
        _draw_corner_brackets(canvas, accent)

        d = ImageDraw.Draw(canvas)
        # Big centered profile name. Auto-scale down if it'd overflow.
        big = _render_text(name_upper, scale=6, color=accent)
        if big.width > SCREEN_W - 40:
            big = _render_text(name_upper, scale=5, color=accent)
        if big.width > SCREEN_W - 40:
            big = _render_text(name_upper, scale=4, color=accent)
        name_y = (SCREEN_H - big.height) // 2 - 14
        _paste_centered_x(canvas, big, name_y)
        # Accent rule under the name
        rule_y = name_y + big.height + 10
        d.line([(70, rule_y), (SCREEN_W - 70, rule_y)], fill=accent, width=2)
        # Tagline below
        tag = _render_text("SLATE CONTROLLER", scale=2, color=MUTED)
        _paste_centered_x(canvas, tag, rule_y + 12)
        # Top-left HUD marker
        marker = _render_text("// PROFILE", scale=2, color=DIM)
        _paste_at(canvas, marker, 22, 16)
        # Top-right cyber code
        code = _render_text("█ ▓ ▒ ░", scale=2, color=_mix(accent, BG, 0.6))
        _paste_at(canvas, code, SCREEN_W - code.width - 22, 16)

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def seed_default_wallpapers_if_missing(
    profile: "Profile", store: "WallpaperStore"
) -> dict[str, bool]:
    """Generate + insert both kinds in the store IFF the slot is empty.

    Custom user uploads are never touched. Returns {kind: True} when a
    default was materialised, {kind: False} when an existing row was
    preserved. Safe to call repeatedly (idempotent).
    """
    out: dict[str, bool] = {}
    for kind in ("home", "lock"):
        existing = await store.get_meta(profile.name, kind=kind)
        if existing is not None:
            out[kind] = False
            continue
        png = generate_default_wallpaper(profile, kind)  # type: ignore[arg-type]
        await store.upsert(
            profile.name, png, "image/png",
            kind=kind, fit_mode="cover",
        )
        out[kind] = True
    return out


# Lazy import (avoid circular at module load — WallpaperStore depends on
# the ORM which imports plenty of stuff).
from app.profiles.wallpapers import WallpaperStore  # noqa: E402, F401  (re-export for typing)
