"""Live framebuffer capture from /dev/fb0 → PNG (landscape 320×240).

Used by the Slate Screen mirror page to see EXACTLY what the panel shows
right now — including GL.iNet's UI widgets overlaid on whatever wallpaper
is set. That mirror is what lets the user identify the "safe zones" for
text on a wallpaper (i.e., where no widget covers the background).

Pipeline:
  1. SSH `cat /dev/fb0` → 153 600 bytes (240×320 RGB565 LE).
  2. RGB565 → RGB888 in pure Python (no numpy).
  3. PIL image portrait 240×320 → rotate -90° → landscape 320×240.
  4. Save as PNG, return bytes.
"""

from __future__ import annotations

import io

import structlog
from PIL import Image

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

PANEL_W = 240
PANEL_H = 320
FB_PATH = "/dev/fb0"
EXPECTED_RAW_SIZE = PANEL_W * PANEL_H * 2  # 153_600 bytes


def _rgb565_to_rgb888(raw: bytes) -> bytes:
    """Convert little-endian RGB565 buffer to RGB888 (one byte triplet per pixel).

    ~150 ms for 76 800 pixels in pure Python — acceptable for an on-demand
    mirror refresh that's polled at ≥1 s intervals from the UI.
    """
    n = len(raw) // 2
    out = bytearray(n * 3)
    for i in range(n):
        # Little-endian: low byte first.
        lo = raw[i * 2]
        hi = raw[i * 2 + 1]
        pixel = (hi << 8) | lo
        # RGB565: RRRRRGGG GGGBBBBB
        r5 = (pixel >> 11) & 0x1F
        g6 = (pixel >> 5) & 0x3F
        b5 = pixel & 0x1F
        # Expand to 8-bit by replicating high bits — better than naive shift.
        r = (r5 << 3) | (r5 >> 2)
        g = (g6 << 2) | (g6 >> 4)
        b = (b5 << 3) | (b5 >> 2)
        out[i * 3] = r
        out[i * 3 + 1] = g
        out[i * 3 + 2] = b
    return bytes(out)


async def capture_screen_png(ssh: SlateSSH) -> bytes:
    """Capture /dev/fb0 → landscape 320×240 PNG bytes ready to send to the UI."""
    raw = await ssh.run_binary(f"cat {FB_PATH}", timeout=10.0)
    if len(raw) < EXPECTED_RAW_SIZE:
        raise SlateSSHError(
            f"framebuffer read short: got {len(raw)} bytes, expected {EXPECTED_RAW_SIZE}"
        )
    raw = raw[:EXPECTED_RAW_SIZE]
    rgb = _rgb565_to_rgb888(raw)
    img = Image.frombytes("RGB", (PANEL_W, PANEL_H), rgb)
    # Portrait → landscape (inverse of the +90° we use when pushing to fb).
    img = img.rotate(-90, expand=True)  # 240×320 → 320×240
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    payload = out.getvalue()
    logger.info("slate.screen_capture", bytes=len(payload))
    return payload
