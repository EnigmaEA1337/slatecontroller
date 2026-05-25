"""Status overlay rendered as a console/terminal card on the wallpaper background.

Visual treatment:
  - Wallpaper-style backdrop (gradient + corner brackets + `[ // STATUS ]` tag)
  - A centered terminal box, like a tiny shell window:
      header bar with `>_ CONSOLE — slate-controller`
      body with a monospace shell prompt line: `$> {title}`
      optional muted second line for the subtitle
      static cursor block at the end of the prompt
  - Optional target pill bottom-right

Uses the Slate's TTF fonts (fetched + cached) — default_mono_medium for
the shell body so the prompt looks like a real terminal.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Literal

import structlog
from PIL import Image, ImageDraw, ImageFont

from app.profiles.font_cache import fetch_font
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

W = 320
H = 240

# Palette matched with wallpaper_studio.py + frontend/src/index.css
BG = (6, 6, 15)
BG_2 = (10, 10, 24)
ACCENT = (255, 58, 82)
FG = (222, 224, 235)
MUTED = (122, 125, 150)
DIM = (77, 79, 107)
OK_GREEN = (90, 232, 168)


MessageKind = Literal["status", "action", "error", "ok"]


@dataclass
class StatusOverlayReport:
    pushed: bool = False
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "pushed": self.pushed,
            "changes": self.changes,
            "errors": self.errors,
        }


def _mix(a, b, w: float):
    return (
        round(a[0] + (b[0] - a[0]) * w),
        round(a[1] + (b[1] - a[1]) * w),
        round(a[2] + (b[2] - a[2]) * w),
    )


def _draw_corner(
    d: ImageDraw.ImageDraw, x: int, y: int, sx: int, sy: int,
    length: int, color: tuple[int, int, int],
) -> None:
    d.line([(x, y), (x + sx * length, y)], fill=color, width=2)
    d.line([(x, y), (x, y + sy * length)], fill=color, width=2)


def _draw_bracketed_label(
    d: ImageDraw.ImageDraw, x: int, y: int, label: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color: tuple[int, int, int],
) -> tuple[int, int]:
    bbox = d.textbbox((0, 0), label, font=font)
    th = bbox[3] - bbox[1]
    pad = 6
    d.text((x, y), "[", fill=color, font=font)
    lb_w = (d.textbbox((0, 0), "[", font=font))[2]
    label_x = x + lb_w + pad
    d.text((label_x, y), label, fill=color, font=font)
    label_w = (d.textbbox((0, 0), label, font=font))[2]
    rb_x = label_x + label_w + pad
    d.text((rb_x, y), "]", fill=color, font=font)
    rb_w = (d.textbbox((0, 0), "]", font=font))[2]
    return rb_x + rb_w, y + th


def _draw_cyber_lines(
    d: ImageDraw.ImageDraw, anchor_x: int, anchor_y: int,
    color: tuple[int, int, int],
) -> None:
    end_x = W - 24
    notch = anchor_y - 12
    d.line([(anchor_x, anchor_y), (end_x - 30, anchor_y)], fill=color, width=1)
    d.line([(end_x - 30, anchor_y), (end_x - 30, notch)], fill=color, width=1)
    d.line([(end_x - 30, notch), (end_x, notch)], fill=color, width=1)
    d.rectangle([(end_x - 32, notch - 2), (end_x - 28, notch + 2)], fill=color)


def _draw_status_pill(
    d: ImageDraw.ImageDraw, x: int, y: int, label: str,
    dot_color: tuple[int, int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    bbox = d.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = 8
    pad_y = 3
    dot_r = 3
    pill_w = pad_x + dot_r * 2 + 6 + tw + pad_x
    pill_h = th + pad_y * 2 + 2
    d.rectangle(
        [(x, y), (x + pill_w, y + pill_h)],
        fill=_mix(BG, dot_color, 0.08),
        outline=_mix(BG, dot_color, 0.45), width=1,
    )
    cy = y + pill_h // 2
    d.ellipse(
        [(x + pad_x, cy - dot_r), (x + pad_x + dot_r * 2, cy + dot_r)],
        fill=dot_color,
    )
    d.text((x + pad_x + dot_r * 2 + 6, y + pad_y), label, fill=dot_color, font=font)


KIND_LABELS: dict[MessageKind, str] = {
    "status": "// STATUS",
    "action": "// ACTION",
    "error":  "// ERROR",
    "ok":     "// OK",
}
KIND_COLORS: dict[MessageKind, tuple[int, int, int]] = {
    "status": ACCENT,
    "action": ACCENT,
    "error":  ACCENT,
    "ok":     OK_GREEN,
}


def _wrap_text(
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


async def render_status_image(
    ssh: SlateSSH,
    title: str = "profile is loading",
    subtitle: str = "from slate controller",
    target: str | None = None,
    kind: MessageKind = "status",
) -> bytes:
    """Generate the 320×240 status PNG: wallpaper-style backdrop + centered
    terminal box rendering `$> {title}` as a fake shell prompt."""
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    # Subtle vertical gradient — same as wallpaper.
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=_mix(BG, BG_2, t * 0.7))

    # Corner brackets matching the wallpaper.
    bk_len = 14
    bk_off = 6
    _draw_corner(d, bk_off, bk_off, 1, 1, bk_len, ACCENT)
    _draw_corner(d, W - bk_off, bk_off, -1, 1, bk_len, ACCENT)
    _draw_corner(d, bk_off, H - bk_off, 1, -1, bk_len, ACCENT)
    _draw_corner(d, W - bk_off, H - bk_off, -1, -1, bk_len, ACCENT)

    # Fetch fonts (TTF from Slate, cached).
    font_med_path = await fetch_font(ssh, "default_medium")
    font_bold_path = await fetch_font(ssh, "default_bold")
    try:
        font_mono_path = await fetch_font(ssh, "default_mono_medium")
    except Exception:  # noqa: BLE001
        font_mono_path = font_med_path  # fallback to medium if mono unavailable
    try:
        f_tag = ImageFont.truetype(str(font_med_path), 11)
        f_pill = ImageFont.truetype(str(font_bold_path), 10)
        f_header = ImageFont.truetype(str(font_med_path), 10)
        f_prompt = ImageFont.truetype(str(font_mono_path), 13)
        f_prompt_sub = ImageFont.truetype(str(font_mono_path), 11)
    except Exception:  # noqa: BLE001
        f_tag = f_pill = f_header = f_prompt = f_prompt_sub = ImageFont.load_default()

    # Top-left tag `[ // STATUS ]` + cyber lines.
    label_text = KIND_LABELS.get(kind, "// STATUS")
    label_color = KIND_COLORS.get(kind, ACCENT)
    right_x, _ = _draw_bracketed_label(d, 18, 18, label_text, f_tag, label_color)
    _draw_cyber_lines(d, right_x + 6, 18 + 6, label_color)

    # ── Terminal box ────────────────────────────────────────────────────
    # Centered in the canvas, leaving room for the top tag + bottom pill.
    box_x0, box_x1 = 18, W - 18
    box_w = box_x1 - box_x0
    # Estimate body height from wrapped prompt lines.
    pad_x = 10
    prompt_prefix = "$> "
    prefix_w = d.textbbox((0, 0), prompt_prefix, font=f_prompt)[2]
    inner_w = box_w - pad_x * 2 - prefix_w - 6
    prompt_lines = _wrap_text(title, f_prompt, d, inner_w) or [""]
    sub_lines = _wrap_text(subtitle or "", f_prompt_sub, d, box_w - pad_x * 2) if subtitle else []
    prompt_lh = 16  # line-height for f_prompt
    sub_lh = 13
    body_h = (
        len(prompt_lines) * prompt_lh
        + (len(sub_lines) * sub_lh + 4 if sub_lines else 0)
    )
    header_h = 18
    box_h = header_h + 10 + body_h + 10
    box_y0 = (H - box_h) // 2
    box_y1 = box_y0 + box_h

    # Box background: a smidge darker than canvas so it reads as overlay.
    inside = _mix(BG, (0, 0, 0), 0.35)
    d.rectangle([(box_x0, box_y0), (box_x1, box_y1)], fill=inside, outline=label_color, width=1)
    # Header bar — slightly different shade, contains the window title.
    d.rectangle(
        [(box_x0, box_y0), (box_x1, box_y0 + header_h)],
        fill=_mix(BG, label_color, 0.10), outline=None,
    )
    # Header bar separator line.
    d.line(
        [(box_x0, box_y0 + header_h), (box_x1, box_y0 + header_h)],
        fill=label_color, width=1,
    )
    # `>_` token + title text inside the header bar.
    chevron = ">_"
    d.text((box_x0 + 8, box_y0 + 4), chevron, fill=label_color, font=f_header)
    chev_w = d.textbbox((0, 0), chevron, font=f_header)[2]
    d.text(
        (box_x0 + 8 + chev_w + 6, box_y0 + 4),
        "CONSOLE — slate-controller",
        fill=MUTED, font=f_header,
    )
    # 3 little "traffic light" dots far-right of the header (cyber-friendly).
    dot_r = 2
    dot_y = box_y0 + header_h // 2
    for i, c in enumerate((DIM, DIM, label_color)):
        cx = box_x1 - 10 - i * 8
        d.ellipse([(cx - dot_r, dot_y - dot_r), (cx + dot_r, dot_y + dot_r)], fill=c)

    # Body: prompt lines.
    body_y = box_y0 + header_h + 8
    for idx, line in enumerate(prompt_lines):
        y = body_y + idx * prompt_lh
        if idx == 0:
            d.text((box_x0 + pad_x, y), prompt_prefix, fill=label_color, font=f_prompt)
            d.text(
                (box_x0 + pad_x + prefix_w, y),
                line, fill=FG, font=f_prompt,
            )
        else:
            # Continuation lines indented under the prompt body.
            d.text((box_x0 + pad_x + prefix_w, y), line, fill=FG, font=f_prompt)

    # Static cursor block after the last prompt line.
    last_line = prompt_lines[-1]
    last_w = d.textbbox((0, 0), last_line, font=f_prompt)[2]
    cur_y0 = body_y + (len(prompt_lines) - 1) * prompt_lh + 1
    cur_x = box_x0 + pad_x + prefix_w + last_w + 3
    d.rectangle([(cur_x, cur_y0), (cur_x + 6, cur_y0 + 12)], fill=label_color)

    # Subtitle / continuation under the prompt block.
    if sub_lines:
        sub_y_start = body_y + len(prompt_lines) * prompt_lh + 4
        for i, line in enumerate(sub_lines):
            d.text(
                (box_x0 + pad_x + prefix_w, sub_y_start + i * sub_lh),
                line, fill=MUTED, font=f_prompt_sub,
            )

    # Target pill bottom-right.
    if target:
        _draw_status_pill(
            d, x=W - 90, y=H - 28, label=target.upper(),
            dot_color=label_color, font=f_pill,
        )

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def push_status_overlay(
    ssh: SlateSSH,
    *,
    title: str = "MISE A JOUR",
    subtitle: str = "depuis Slate Controller",
    target: str | None = None,
    kind: MessageKind = "status",
    restart: bool = True,
) -> StatusOverlayReport:
    """Legacy push path (wallpaper-based). Kept for backward compat — the
    real takeover path now lives in app.profiles.slate_message which uses
    the framebuffer directly."""
    rep = StatusOverlayReport()
    try:
        png = await render_status_image(
            ssh, title=title, subtitle=subtitle, target=target, kind=kind,
        )
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"render: {exc}")
        return rep
    rep.changes.append(f"rendered status image ({len(png)} bytes)")
    try:
        await ssh.put_bytes(png, "/etc/gl_screen/wallpaper_home.png")
        rep.changes.append("pushed status image to wallpaper_home.png")
    except SlateSSHError as exc:
        rep.errors.append(f"upload: {exc}")
        return rep
    rep.pushed = True
    if restart:
        try:
            r = await ssh.run("/etc/init.d/gl_screen restart 2>&1", timeout=15)
            if r.exit_status == 0:
                rep.changes.append("gl_screen restarted")
            else:
                rep.errors.append(f"restart exit={r.exit_status}")
        except SlateSSHError as exc:
            rep.errors.append(f"restart SSH error: {exc}")
    return rep
