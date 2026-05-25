"""Render menu frames for the reset-button profile cycle.

One PNG per cursor position. When the cursor is at slot N, frame N is
painted on the Slate's panel during the select-then-commit window: a
vertical list with slot N highlighted (accent strip + filled chip).

The frames are pre-rendered controller-side (Pillow + the Slate's own
TTF fonts, fetched via `font_cache`) and pushed to the Slate at sync
time, so the on-press code path on the Slate is just `cat raw > fb0`.
The agent has no Python, no Pillow, no fonts.

Aesthetic intent : the controller UI is a cyberpunk HUD ; the on-panel
menu should feel like the same product, not a different system. We use
the same TTF stack that `wallpaper_studio` uses for profile wallpapers,
the same palette as the controller's CSS tokens, corner brackets, an
accent strip on the selected row, and a discreet scanline overlay.
"""

from __future__ import annotations

import io

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
)
from app.profiles.font_cache import fetch_font
from app.settings.button_cycle import CycleStep
from app.slate.ssh import SlateSSH


# How many rows fit comfortably on the panel. Beyond this we paginate
# around the cursor so the highlighted row is always visible.
_MAX_VISIBLE_ROWS = 6

_ROW_H = 28
_TITLE_H = 26
_FOOTER_H = 20

# Tints used only by the menu. Slightly different from the wallpaper
# palette : we want the highlighted row to read as "selected" without
# screaming.
_SELECTED_FILL = (28, 14, 22)        # warm dark, accent-tinted
_SELECTED_BORDER = DEFAULT_ACCENT
_ROW_BG = (16, 16, 28)               # subtle pop against BG
_HEADER_BG = (12, 12, 22)


async def render_menu_frames_async(
    ssh: SlateSSH, steps: list[CycleStep],
) -> list[bytes]:
    """Render one PNG per cursor position. Async because the TTF fonts
    are fetched from the Slate via SSH on first use (cached locally
    after that — see `font_cache.fetch_font`)."""
    fonts = await _load_fonts(ssh)
    return [_render_one(steps, i, fonts) for i in range(len(steps))]


# ---------------------------- internals ---------------------------- #


class _Fonts:
    """Bundle of pre-loaded TTF faces at the sizes the menu uses."""

    def __init__(
        self,
        *,
        title: ImageFont.FreeTypeFont,
        row: ImageFont.FreeTypeFont,
        row_bold: ImageFont.FreeTypeFont,
        small: ImageFont.FreeTypeFont,
        small_mono: ImageFont.FreeTypeFont,
    ) -> None:
        self.title = title
        self.row = row
        self.row_bold = row_bold
        self.small = small
        self.small_mono = small_mono


async def _load_fonts(ssh: SlateSSH) -> _Fonts:
    medium = await fetch_font(ssh, "default_medium")
    bold = await fetch_font(ssh, "default_bold")
    mono = await fetch_font(ssh, "default_mono_medium")
    return _Fonts(
        title=ImageFont.truetype(str(bold), 13),
        row=ImageFont.truetype(str(medium), 13),
        row_bold=ImageFont.truetype(str(bold), 13),
        small=ImageFont.truetype(str(medium), 9),
        small_mono=ImageFont.truetype(str(mono), 9),
    )


def _render_one(
    steps: list[CycleStep], cursor: int, fonts: _Fonts,
) -> bytes:
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), BG)
    d = ImageDraw.Draw(img)

    # Faint scanlines — matches the wallpaper aesthetic.
    for y in range(0, SCREEN_H, 3):
        d.line([(0, y), (SCREEN_W, y)], fill=SCAN)

    _draw_title_bar(d, fonts, cursor=cursor, total=len(steps))
    _draw_corners(d, color=BORDER_STRONG)

    if not steps:
        msg = "no cycle configured"
        w = d.textbbox((0, 0), msg, font=fonts.row)[2]
        d.text(
            ((SCREEN_W - w) // 2, SCREEN_H // 2 - 8),
            msg, fill=MUTED, font=fonts.row,
        )
        _draw_footer(d, fonts, text="open Settings → Agent in the UI")
        return _to_png(img)

    first, last = _visible_window(len(steps), cursor)
    y = _TITLE_H + 6
    for i in range(first, last):
        step = steps[i]
        is_active = (i == cursor)
        _draw_row(d, fonts, y=y, idx=i, step=step, active=is_active)
        y += _ROW_H

    _draw_footer(
        d, fonts,
        text=(
            "press again to advance  ·  release 3s to apply"
            if 0 <= cursor < len(steps)
            else "press reset to start"
        ),
    )
    return _to_png(img)


def _draw_title_bar(
    d: ImageDraw.ImageDraw, fonts: _Fonts, *, cursor: int, total: int,
) -> None:
    d.rectangle([(0, 0), (SCREEN_W, _TITLE_H)], fill=_HEADER_BG)
    d.line([(0, _TITLE_H), (SCREEN_W, _TITLE_H)], fill=BORDER_STRONG)
    # Accent block to anchor the eye on the left.
    d.rectangle([(0, 0), (4, _TITLE_H)], fill=DEFAULT_ACCENT)

    title = "CYCLE"
    d.text((14, 6), title, fill=FG, font=fonts.title)
    title_w = d.textbbox((14, 6), title, font=fonts.title)[2]
    sub = "profile selector"
    d.text((title_w + 8, 9), sub, fill=DIM, font=fonts.small)

    if total > 0:
        counter = (
            f"{cursor + 1:02d}/{total:02d}" if 0 <= cursor < total
            else f"--/{total:02d}"
        )
        cw = d.textbbox((0, 0), counter, font=fonts.small_mono)[2]
        # Counter chip on the right side of the title bar.
        cx = SCREEN_W - cw - 14
        d.rectangle(
            [(cx - 6, 5), (SCREEN_W - 8, 5 + 14)],
            outline=BORDER_STRONG, fill=BG_2,
        )
        d.text((cx - 2, 7), counter, fill=FG, font=fonts.small_mono)


def _visible_window(total: int, cursor: int) -> tuple[int, int]:
    if total <= _MAX_VISIBLE_ROWS:
        return 0, total
    if cursor < 0:
        return 0, _MAX_VISIBLE_ROWS
    half = _MAX_VISIBLE_ROWS // 2
    first = max(0, cursor - half)
    last = first + _MAX_VISIBLE_ROWS
    if last > total:
        last = total
        first = total - _MAX_VISIBLE_ROWS
    return first, last


def _draw_row(
    d: ImageDraw.ImageDraw,
    fonts: _Fonts,
    *,
    y: int,
    idx: int,
    step: CycleStep,
    active: bool,
) -> None:
    margin = 10
    box_top = y
    box_bot = y + _ROW_H - 4

    if active:
        # Accent strip + tinted fill + accent outline. No bullet — the
        # whole row IS the selection indicator.
        d.rectangle(
            [(margin, box_top), (SCREEN_W - margin, box_bot)],
            fill=_SELECTED_FILL,
            outline=_SELECTED_BORDER,
            width=1,
        )
        d.rectangle(
            [(margin, box_top), (margin + 3, box_bot)],
            fill=DEFAULT_ACCENT,
        )
        text_color = FG
        kind_color = DEFAULT_ACCENT
        font = fonts.row_bold
    else:
        # Subtle divider under each non-active row — keeps the list
        # scannable without competing visually with the selection.
        d.rectangle(
            [(margin, box_top), (SCREEN_W - margin, box_bot)],
            fill=_ROW_BG, outline=None,
        )
        d.line(
            [(margin, box_bot), (SCREEN_W - margin, box_bot)],
            fill=BORDER,
        )
        text_color = MUTED
        kind_color = DIM
        font = fonts.row

    # Index pill on the left ("01", "02", …) — uses mono so the digit
    # column doesn't drift between rows.
    pill = f"{idx + 1:02d}"
    d.text((margin + 12, box_top + 6), pill, fill=kind_color, font=fonts.small_mono)

    # Kind hint and step name.
    label = step.name
    name_x = margin + 38
    d.text((name_x, box_top + 5), label, fill=text_color, font=font)

    # Tag on the right ("ACTION" / "PROFILE") so the user can tell at
    # a glance what each slot does.
    tag = "ACTION" if step.kind == "action" else "PROFILE"
    tag_w = d.textbbox((0, 0), tag, font=fonts.small)[2]
    d.text(
        (SCREEN_W - margin - tag_w - 8, box_top + 8),
        tag, fill=kind_color, font=fonts.small,
    )


def _draw_corners(d: ImageDraw.ImageDraw, *, color: tuple[int, int, int]) -> None:
    """L-shaped corner brackets in the four corners of the canvas.

    Matches the controller's cyber-card look — tiny detail, big effect."""
    L = 10
    M = 2
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
    d.line(
        [(SCREEN_W - M, SCREEN_H - M), (SCREEN_W - M - L, SCREEN_H - M)],
        fill=color, width=1,
    )
    d.line(
        [(SCREEN_W - M, SCREEN_H - M), (SCREEN_W - M, SCREEN_H - M - L)],
        fill=color, width=1,
    )


def _draw_footer(
    d: ImageDraw.ImageDraw, fonts: _Fonts, *, text: str,
) -> None:
    fy = SCREEN_H - _FOOTER_H
    d.rectangle([(0, fy), (SCREEN_W, SCREEN_H)], fill=_HEADER_BG)
    d.line([(0, fy), (SCREEN_W, fy)], fill=BORDER_STRONG)
    d.text((10, fy + 5), text, fill=DIM, font=fonts.small)


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
