"""Render menu frames for the reset-button profile cycle.

One PNG per cursor position. When the cursor is at slot N, the menu
frame N is shown on the Slate's panel: a vertical list of every step
in the cycle, with slot N highlighted (accent border + filled chip).

The frames are pre-rendered controller-side (Pillow) and pushed to the
Slate at sync time, so the on-press code path on the Slate is just a
file copy → /dev/fb0. The agent has no Python, no Pillow, no font.

Layout (320×240, landscape, then `_png_to_rgb565_portrait` rotates
to the panel's native 240×320 portrait orientation) :

  ┌─ CYCLE                    3 / 5 ─┐
  │                                   │
  │   ◌  home                         │
  │   ◌  mission                      │
  │   ▶  vacances              ◀──    │  ← highlighted
  │   ◌  @update                      │
  │   ◌  osint                        │
  │                                   │
  │  release reset to apply           │
  └───────────────────────────────────┘

The accent color follows the cycle highlight, not the profile colors.
"""

from __future__ import annotations

import io
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from app.profiles.default_wallpaper import (
    BG,
    BG_2,
    BORDER,
    BORDER_STRONG,
    DEFAULT_ACCENT,
    DIM,
    FG,
    MUTED,
    SCAN,
    SCREEN_H,
    SCREEN_W,
    _render_text,
)
from app.settings.button_cycle import CycleStep


# How many rows fit comfortably on the panel. Beyond this we paginate
# around the cursor so the highlighted row is always visible.
_MAX_VISIBLE_ROWS = 6


def render_menu_png(
    steps: list[CycleStep],
    cursor: int,
) -> bytes:
    """Render PNG bytes for the menu with `cursor` slot highlighted.

    Out of bounds cursor → highlights nothing (idle preview). Empty
    `steps` → "no cycle configured" placeholder, useful so the cycle
    script can paint *something* coherent if it's invoked before the
    user has populated the list.
    """
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), BG)
    d = ImageDraw.Draw(img)

    # Faint scanlines for the retro feel (mirrors default_wallpaper).
    for y in range(0, SCREEN_H, 3):
        d.line([(0, y), (SCREEN_W, y)], fill=SCAN)

    # ── Title bar ────────────────────────────────────────────────
    title_h = 22
    d.rectangle([(0, 0), (SCREEN_W, title_h)], fill=BG_2)
    d.line([(0, title_h), (SCREEN_W, title_h)], fill=BORDER_STRONG)
    _paste_text(img, "CYCLE", scale=2, color=FG, pos=(10, 4))
    counter = (
        f"{cursor + 1} / {len(steps)}"
        if 0 <= cursor < len(steps)
        else f"— / {len(steps)}"
    )
    counter_img = _text_img(counter, scale=1, color=MUTED)
    img.paste(counter_img, (SCREEN_W - counter_img.width - 10, 6))

    if not steps:
        _paste_text(
            img,
            "no cycle configured",
            scale=2,
            color=MUTED,
            pos=(20, SCREEN_H // 2 - 8),
        )
        return _to_png(img)

    # ── Step list (paginated around cursor) ─────────────────────
    first, last = _visible_window(len(steps), cursor)
    row_h = 26
    y = title_h + 10
    for i in range(first, last):
        step = steps[i]
        is_active = (i == cursor)
        _draw_row(d, img, y=y, row_h=row_h, idx=i, step=step, active=is_active)
        y += row_h

    # ── Footer hint ─────────────────────────────────────────────
    footer_h = 18
    fy = SCREEN_H - footer_h
    d.rectangle([(0, fy), (SCREEN_W, SCREEN_H)], fill=BG_2)
    d.line([(0, fy), (SCREEN_W, fy)], fill=BORDER_STRONG)
    if 0 <= cursor < len(steps):
        hint = "release reset to apply"
    else:
        hint = "press reset to start"
    _paste_text(img, hint, scale=1, color=DIM, pos=(8, fy + 4))

    return _to_png(img)


def render_menu_frames(steps: list[CycleStep]) -> list[bytes]:
    """One PNG per cursor position. Returns `len(steps)` frames.

    Empty `steps` → empty list (no frames to push; the cycle script
    falls back to its silent log path).
    """
    return [render_menu_png(steps, i) for i in range(len(steps))]


# ---------------------------- helpers ---------------------------- #


def _visible_window(total: int, cursor: int) -> tuple[int, int]:
    """Pick a [first, last) slice of `total` steps that keeps `cursor`
    in view. Used when the list is longer than _MAX_VISIBLE_ROWS."""
    if total <= _MAX_VISIBLE_ROWS:
        return 0, total
    if cursor < 0:
        return 0, _MAX_VISIBLE_ROWS
    # Try to centre cursor in the window when possible.
    half = _MAX_VISIBLE_ROWS // 2
    first = max(0, cursor - half)
    last = first + _MAX_VISIBLE_ROWS
    if last > total:
        last = total
        first = total - _MAX_VISIBLE_ROWS
    return first, last


def _draw_row(
    d: ImageDraw.ImageDraw,
    img: Image.Image,
    *,
    y: int,
    row_h: int,
    idx: int,
    step: CycleStep,
    active: bool,
) -> None:
    margin_x = 10
    box_top = y
    box_bot = y + row_h - 4

    if active:
        # Accent strip on the left + softer fill on the row.
        d.rectangle([(0, box_top), (4, box_bot)], fill=DEFAULT_ACCENT)
        d.rectangle(
            [(margin_x - 4, box_top), (SCREEN_W - margin_x, box_bot)],
            fill=(20, 14, 24),
            outline=DEFAULT_ACCENT,
            width=1,
        )
        bullet_color = DEFAULT_ACCENT
        text_color = FG
        bullet = "▶"  # ▶
    else:
        d.line(
            [(margin_x, box_bot), (SCREEN_W - margin_x, box_bot)],
            fill=BORDER,
        )
        bullet_color = DIM
        text_color = MUTED
        bullet = "◌"  # ◌

    # Bullet — pre-rendered text. Fits nicely with scale=1.
    _paste_text(img, bullet, scale=2, color=bullet_color, pos=(margin_x + 4, y + 4))

    # Step kind prefix : profiles get nothing, actions get @ so the user
    # spots them visually as different.
    label = step.name if step.kind == "profile" else f"@{step.name}"
    _paste_text(img, label, scale=2, color=text_color, pos=(margin_x + 28, y + 4))


def _text_img(text: str, *, scale: int, color: tuple[int, int, int]) -> Image.Image:
    """Same as `_render_text` but tagged with a clearer name here."""
    return _render_text(text, scale=scale, color=color)


def _paste_text(
    img: Image.Image,
    text: str,
    *,
    scale: int,
    color: tuple[int, int, int],
    pos: tuple[int, int],
) -> None:
    tile = _text_img(text, scale=scale, color=color)
    img.paste(tile, pos)


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
