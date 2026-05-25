"""Wallpaper generator matching the Slate Controller cyberpunk theme.

The wallpaper acts as a profile-tree HUD: instead of a giant profile name
in the middle (which conflicts with GL.iNet's overlay widgets), we list
the 5 base profiles + an "OTHERS" bucket as a left-side tree. The active
profile's row is in accent red; the others stay in muted text. That way
the screen always says which profile is loaded without obstructing the
panel UI, and works for custom profiles too via the OTHERS row.

The controller UI's visual language is replicated here:
  - Red accent (#ff3a52) for the active row, brackets, corner marks.
  - Square brackets `[ // PROFILES ]` for the section header.
  - Corner brackets at the four edges.
  - Cyber lines extending from the header toward the right edge.
  - Profile color → status pill dot only.
"""

from __future__ import annotations

import io
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from app.models.profile import Profile
from app.profiles.font_cache import fetch_font
from app.slate.ssh import SlateSSH

WallpaperKind = Literal["home", "lock"]

W = 320
H = 240

# Controller palette (frontend/src/index.css, verified pixel-by-pixel)
BG = (6, 6, 15)
BG_2 = (10, 10, 24)
ACCENT = (255, 58, 82)        # --color-cyber-accent (red)
ACCENT_DIM = (184, 42, 58)    # accent-dim
FG = (222, 224, 235)          # primary text
MUTED = (122, 125, 150)
DIM = (77, 79, 107)
OK_GREEN = (90, 232, 168)     # --color-cyber-ok

# The 5 built-in profiles (CLAUDE.md). Anything outside this set is grouped
# under the synthetic "OTHERS" row in the tree.
BASE_PROFILES: tuple[str, ...] = ("mission", "vacances", "osint", "home", "lockdown")


def _mix(a, b, w: float):
    return (
        round(a[0] + (b[0] - a[0]) * w),
        round(a[1] + (b[1] - a[1]) * w),
        round(a[2] + (b[2] - a[2]) * w),
    )


def _hex_to_rgb(h: str | None) -> tuple[int, int, int]:
    if not h:
        return OK_GREEN
    h = h.strip().lstrip("#")
    if len(h) != 6:
        return OK_GREEN
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return OK_GREEN


def _draw_corner(d: ImageDraw.ImageDraw, x: int, y: int, sx: int, sy: int,
                 length: int, color: tuple[int, int, int]) -> None:
    """L-shaped corner bracket. sx/sy = ±1 for direction."""
    d.line([(x, y), (x + sx * length, y)], fill=color, width=2)
    d.line([(x, y), (x, y + sy * length)], fill=color, width=2)


def _draw_bracketed_label(
    d: ImageDraw.ImageDraw, x: int, y: int, label: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color: tuple[int, int, int], bracket_color: tuple[int, int, int] | None = None,
) -> tuple[int, int]:
    """Render `[ LABEL ]` and return (right_x, bottom_y) of the rendered block."""
    bc = bracket_color or color
    bbox = d.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad = 6
    # Left bracket
    d.text((x, y), "[", fill=bc, font=font)
    lb_w = (d.textbbox((0, 0), "[", font=font))[2]
    # Label
    label_x = x + lb_w + pad
    d.text((label_x, y), label, fill=color, font=font)
    # Right bracket
    rb_x = label_x + tw + pad
    d.text((rb_x, y), "]", fill=bc, font=font)
    rb_w = (d.textbbox((0, 0), "]", font=font))[2]
    return rb_x + rb_w, y + th


def _draw_cyber_lines(
    d: ImageDraw.ImageDraw, anchor_x: int, anchor_y: int, color: tuple[int, int, int],
    side: Literal["right", "left"] = "right",
) -> None:
    """The angular line decorations that flank UI titles in the controller.

    Two horizontal strokes joined by an L-bend toward the canvas edge —
    like a circuit trace heading off the panel.
    """
    if side == "right":
        # Horizontal stub from anchor, then bend up, then off-screen right.
        end_x = W - 4
        notch = anchor_y - 14
        d.line([(anchor_x, anchor_y), (end_x - 32, anchor_y)], fill=color, width=1)
        d.line([(end_x - 32, anchor_y), (end_x - 32, notch)], fill=color, width=1)
        d.line([(end_x - 32, notch), (end_x, notch)], fill=color, width=1)
        # Tiny tick at the corner
        d.rectangle([(end_x - 34, notch - 2), (end_x - 30, notch + 2)], fill=color)
    else:
        start_x = 4
        notch = anchor_y - 14
        d.line([(anchor_x, anchor_y), (start_x + 32, anchor_y)], fill=color, width=1)
        d.line([(start_x + 32, anchor_y), (start_x + 32, notch)], fill=color, width=1)
        d.line([(start_x + 32, notch), (start_x, notch)], fill=color, width=1)
        d.rectangle([(start_x + 30, notch - 2), (start_x + 34, notch + 2)], fill=color)


def _draw_status_pill(
    d: ImageDraw.ImageDraw, x: int, y: int, label: str,
    dot_color: tuple[int, int, int], font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    """The `● ONLINE`-style pill with colored dot + label."""
    bbox = d.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = 8
    pad_y = 3
    dot_r = 3
    pill_w = pad_x + dot_r * 2 + 6 + tw + pad_x
    pill_h = th + pad_y * 2 + 2
    # Background with subtle border
    d.rectangle(
        [(x, y), (x + pill_w, y + pill_h)],
        fill=_mix(BG, dot_color, 0.08),
        outline=_mix(BG, dot_color, 0.45),
        width=1,
    )
    # Dot
    cy = y + pill_h // 2
    d.ellipse(
        [(x + pad_x, cy - dot_r), (x + pad_x + dot_r * 2, cy + dot_r)],
        fill=dot_color,
    )
    # Label
    d.text((x + pad_x + dot_r * 2 + 6, y + pad_y), label, fill=dot_color, font=font)


def _draw_profile_tree(
    d: ImageDraw.ImageDraw,
    active_key: str,
    *,
    x0: int,
    y0: int,
    row_h: int,
    f_row: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    f_dot: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> int:
    """Draw the profile tree starting at (x0, y0). Returns bottom Y."""
    rows = list(BASE_PROFILES) + ["others"]
    trunk_x = x0 + 4
    branch_len = 10
    label_x = trunk_x + branch_len + 6

    # Vertical trunk — DIM color, runs from first row mid-Y to last row mid-Y.
    first_mid = y0 + row_h // 2
    last_mid = y0 + (len(rows) - 1) * row_h + row_h // 2
    d.line([(trunk_x, first_mid), (trunk_x, last_mid)], fill=DIM, width=1)

    for i, key in enumerate(rows):
        is_active = key == active_key
        row_y = y0 + i * row_h
        mid_y = row_y + row_h // 2
        is_last = i == len(rows) - 1

        # Branch line. Active row → red accent. Others → DIM.
        branch_color = ACCENT if is_active else DIM
        # For the last row, also redraw the trunk segment up to it (so the
        # corner looks like └ instead of ├).
        d.line(
            [(trunk_x, mid_y), (trunk_x + branch_len, mid_y)],
            fill=branch_color, width=1,
        )
        # Small notch at the branch tip (matches UI cyber-lines style).
        d.rectangle(
            [(trunk_x + branch_len - 1, mid_y - 1),
             (trunk_x + branch_len + 1, mid_y + 1)],
            fill=branch_color,
        )

        # Active row: leading dot, bold label, red.
        # Inactive rows: muted label, no dot.
        label = key.upper()
        if is_active:
            # Filled dot to the left of label
            dot_r = 2
            d.ellipse(
                [(label_x, mid_y - dot_r), (label_x + dot_r * 2, mid_y + dot_r)],
                fill=ACCENT,
            )
            text_x = label_x + dot_r * 2 + 5
            color = ACCENT
        else:
            text_x = label_x
            # "others" is even more muted than inactive base profiles.
            color = DIM if key == "others" else MUTED

        # Center label vertically on mid_y.
        tb = d.textbbox((0, 0), label, font=f_row)
        th = tb[3] - tb[1]
        d.text((text_x, mid_y - th // 2 - 1), label, fill=color, font=f_row)
        _ = is_last  # explicit marker; styling identical otherwise

    return last_mid + row_h // 2


def _wrap_text_w(
    text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    d: ImageDraw.ImageDraw, max_width: int,
) -> list[str]:
    """Greedy word-wrap to `max_width` pixels."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if d.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_terminal_box(
    d: ImageDraw.ImageDraw,
    *,
    cx: int, cy: int,
    box_w: int,
    body_lines: list[tuple[str, tuple[int, int, int]]],
    header_label: str,
    accent: tuple[int, int, int],
    f_header: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    f_body: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    prompt_prefix: str = "$> ",
) -> tuple[int, int]:
    """Render a centered console-style box. Body lines are tuples of
    (text, color); the first body line gets the prompt prefix in accent."""
    prompt_w = d.textbbox((0, 0), prompt_prefix, font=f_body)[2]
    body_lh = 16
    header_h = 18
    pad_x = 10
    pad_top = 8
    pad_bottom = 8
    body_h = len(body_lines) * body_lh
    box_h = header_h + pad_top + body_h + pad_bottom
    box_x0 = cx - box_w // 2
    box_y0 = cy - box_h // 2
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h

    # Background — slightly darker than the canvas behind.
    inside = _mix(BG, (0, 0, 0), 0.40)
    d.rectangle(
        [(box_x0, box_y0), (box_x1, box_y1)],
        fill=inside, outline=accent, width=1,
    )
    # Header bar
    d.rectangle(
        [(box_x0, box_y0), (box_x1, box_y0 + header_h)],
        fill=_mix(BG, accent, 0.12), outline=None,
    )
    d.line(
        [(box_x0, box_y0 + header_h), (box_x1, box_y0 + header_h)],
        fill=accent, width=1,
    )
    chev = ">_"
    d.text((box_x0 + 8, box_y0 + 4), chev, fill=accent, font=f_header)
    chev_w = d.textbbox((0, 0), chev, font=f_header)[2]
    d.text(
        (box_x0 + 8 + chev_w + 6, box_y0 + 4),
        header_label, fill=MUTED, font=f_header,
    )
    # Traffic-light dots far-right.
    dot_r = 2
    dy = box_y0 + header_h // 2
    for i, c in enumerate((DIM, DIM, accent)):
        cx_dot = box_x1 - 10 - i * 8
        d.ellipse(
            [(cx_dot - dot_r, dy - dot_r), (cx_dot + dot_r, dy + dot_r)],
            fill=c,
        )

    # Body: first line gets the prompt prefix, continuation lines indent.
    body_y = box_y0 + header_h + pad_top
    for i, (line, color) in enumerate(body_lines):
        y = body_y + i * body_lh
        if i == 0:
            d.text((box_x0 + pad_x, y), prompt_prefix, fill=accent, font=f_body)
            d.text((box_x0 + pad_x + prompt_w, y), line, fill=color, font=f_body)
        else:
            d.text((box_x0 + pad_x + prompt_w, y), line, fill=color, font=f_body)
    # Static cursor at the end of the last line.
    last_line = body_lines[-1][0] if body_lines else ""
    last_w = d.textbbox((0, 0), last_line, font=f_body)[2]
    cur_y0 = body_y + (len(body_lines) - 1) * body_lh + 1
    cur_x = box_x0 + pad_x + prompt_w + last_w + 3
    d.rectangle([(cur_x, cur_y0), (cur_x + 6, cur_y0 + 12)], fill=accent)
    return box_x0, box_y1


async def render_wallpaper(
    profile: Profile, kind: WallpaperKind, ssh: SlateSSH,
) -> bytes:
    """Render a 320×240 RGB PNG matching the controller cyber theme.

    `home` kind: profile-tree HUD with the active row highlighted; kept
    clear in the center so GL.iNet widgets land on top cleanly.
    `lock` kind: terminal-style locked message; no profile info exposed —
    just a console-card asking to unlock the communication terminal.
    """
    profile_dot = _hex_to_rgb(profile.color)
    # Determine which tree row is "active": the profile name itself if it's
    # one of the 5 builtins, otherwise the synthetic "others" bucket.
    active_key = profile.name if profile.name in BASE_PROFILES else "others"

    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    # Subtle vertical gradient.
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=_mix(BG, BG_2, t * 0.7))

    # Fetch fonts (TTF from the Slate, cached).
    font_med_path = await fetch_font(ssh, "default_medium")
    font_bold_path = await fetch_font(ssh, "default_bold")
    try:
        font_mono_path = await fetch_font(ssh, "default_mono_medium")
    except Exception:  # noqa: BLE001
        font_mono_path = font_med_path
    try:
        f_tag = ImageFont.truetype(str(font_med_path), 11)
        f_small = ImageFont.truetype(str(font_med_path), 10)
        f_pill = ImageFont.truetype(str(font_bold_path), 10)
        f_row = ImageFont.truetype(str(font_bold_path), 12)
        f_row_dot = ImageFont.truetype(str(font_bold_path), 10)
        f_header = ImageFont.truetype(str(font_med_path), 10)
        f_body_mono = ImageFont.truetype(str(font_mono_path), 12)
    except Exception:  # noqa: BLE001
        f_tag = f_small = f_pill = f_row = f_row_dot = ImageFont.load_default()
        f_header = f_body_mono = f_tag

    # Corner brackets (all four corners, red accent).
    bk_len = 14
    bk_off = 6
    _draw_corner(d, bk_off, bk_off, 1, 1, bk_len, ACCENT)
    _draw_corner(d, W - bk_off, bk_off, -1, 1, bk_len, ACCENT)
    _draw_corner(d, bk_off, H - bk_off, 1, -1, bk_len, ACCENT)
    _draw_corner(d, W - bk_off, H - bk_off, -1, -1, bk_len, ACCENT)

    if kind == "home":
        # Top-left header `[ // PROFILES ]` with cyber lines.
        right_x, _ = _draw_bracketed_label(
            d, 18, 18, "// PROFILES", f_tag, ACCENT,
        )
        _draw_cyber_lines(d, right_x + 6, 18 + 6, ACCENT, side="right")

        # Profile tree (left column). 6 rows × ~22px = 132px, starts y=46.
        _draw_profile_tree(
            d, active_key=active_key,
            x0=20, y0=46, row_h=22,
            f_row=f_row, f_dot=f_row_dot,
        )

        # Small SLATE:// breadcrumb in the bottom-left.
        # Center/right area kept clear for GL.iNet widgets.
        d.text(
            (18, H - 22),
            "SLATE://" + profile.name.upper(),
            fill=DIM, font=f_small,
        )

    else:
        # LOCK SCREEN — zero info disclosure. Just a "system locked"
        # terminal card centered on the cyber backdrop.
        right_x, _ = _draw_bracketed_label(
            d, 18, 18, "// ACCESS", f_tag, ACCENT,
        )
        _draw_cyber_lines(d, right_x + 6, 18 + 6, ACCENT, side="right")

        # Centered terminal box with the lock message.
        # Body line 1 (with $> prefix): "system locked"
        # Body line 2 (continuation): "please unlock the communication terminal"
        # Wrap the second sentence to fit the box.
        box_w = W - 32  # 16px margin each side
        # Inside width for wrapping the continuation line:
        prompt_w = d.textbbox((0, 0), "$> ", font=f_body_mono)[2]
        inner_w = box_w - 20 - prompt_w  # 10px padding each side
        sub_lines = _wrap_text_w(
            "please unlock the communication terminal",
            f_body_mono, d, inner_w,
        )
        body: list[tuple[str, tuple[int, int, int]]] = [
            ("system locked", FG),
        ]
        for line in sub_lines:
            body.append((line, MUTED))

        _draw_terminal_box(
            d,
            cx=W // 2, cy=H // 2 + 4,
            box_w=box_w,
            body_lines=body,
            header_label="CONSOLE — slate-controller",
            accent=ACCENT,
            f_header=f_header,
            f_body=f_body_mono,
        )

        # Bottom-right small lock indicator pill.
        _draw_status_pill(
            d, x=W - 78, y=H - 28, label="LOCKED",
            dot_color=ACCENT, font=f_pill,
        )
        _ = profile_dot  # unused on lock — no profile color leakage

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()
