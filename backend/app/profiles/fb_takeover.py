"""Direct framebuffer takeover for arbitrary on-screen messages.

Why this exists: gl_screen draws GL.iNet's own UI cards/widgets ON TOP of
the wallpaper at /etc/gl_screen/wallpaper_home.png. Replacing that file
changes the visible *background*, which is mostly hidden by widgets — so
status messages there are invisible. To show a *real* arbitrary message,
we kick gl_screen out of the way and write our pixels directly to /dev/fb0.

Pipeline:
  1. Stop gl_screen via procd (`/etc/init.d/gl_screen stop`).
  2. Belt-and-braces: `killall -9 gl_screen` (busybox doesn't ship pkill)
     to make sure no stray copy stays drawing. procd's stop should suffice
     but procd respawns on its 5s timer; we kill on every loop iteration.
  3. Convert our PIL image (landscape 320×240) → portrait 240×320 RGB565
     little-endian, 153600 raw bytes — the panel's native format.
  4. Stream the raw bytes onto /dev/fb0 via SSH stdin pipe.
  5. CONTINUOUS-WRITE LOOP for `duration_seconds`. Every ~600 ms we kill
     any newcomer + re-write the fb. This makes the hold robust against
     procd respawning gl_screen during the window (which it does on this
     firmware — `respawn 1 5 -1` means a kill triggers a restart attempt
     within ~5s) and against any other writer racing for /dev/fb0.
  6. Start gl_screen back. It repaints with its normal UI.

Limitations:
  - During the takeover (~N+2s) the GL.iNet UI is unresponsive — touch
    events are not processed.
  - The fb image stays on screen until something overwrites it, so we
    MUST restart gl_screen at the end (otherwise the message stays stuck
    when the activate completes).
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field

import structlog
from PIL import Image

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

# Native panel orientation from /sys/class/graphics/fb0/:
#   bits_per_pixel = 16  (RGB565)
#   virtual_size   = 240,320  (portrait)
#   stride         = 480
# Total raw size = 240 * 320 * 2 = 153_600 bytes.
PANEL_W = 240
PANEL_H = 320
FB_PATH = "/dev/fb0"


@dataclass
class FbTakeoverReport:
    ok: bool = True
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok and not self.errors, "steps": self.steps, "errors": self.errors}


def _png_to_rgb565_portrait(png_bytes: bytes) -> bytes:
    """Convert a PNG (any size, any mode) to 240×320 RGB565 little-endian.

    Source images are typically landscape 320×240 — we rotate 90° clockwise
    to match the panel's portrait orientation. Returns exactly 153 600 bytes.
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    # Source landscape 320×240 → portrait 240×320 for the panel's native
    # memory layout. The ST7789 on this Slate is mounted such that the
    # framebuffer (0,0) maps to what the user sees as the top-LEFT corner
    # when looking at the device in landscape — so we need a +90° rotation
    # (counter-clockwise in PIL) to land "top" of the source at "left" of
    # the panel. Tested live with directional markers.
    if img.size == (320, 240):
        img = img.rotate(90, expand=True)
    if img.size != (PANEL_W, PANEL_H):
        img = img.resize((PANEL_W, PANEL_H), Image.Resampling.LANCZOS)

    # RGB888 → RGB565 little-endian, pure-Python because numpy isn't in the
    # backend image. ~150ms for 76800 pixels on a modern host, acceptable
    # for an on-demand UI op the user is already waiting on.
    raw = img.tobytes()  # length = 240 * 320 * 3
    out = bytearray(PANEL_W * PANEL_H * 2)
    for i in range(PANEL_W * PANEL_H):
        r = raw[i * 3]
        g = raw[i * 3 + 1]
        b = raw[i * 3 + 2]
        pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i * 2] = pixel & 0xFF
        out[i * 2 + 1] = (pixel >> 8) & 0xFF
    return bytes(out)


# Local refresh interval on the Slate (busybox usleep, microseconds).
# 120ms is fast enough that procd's respawn never produces a visible frame:
# the new process gets pkill'd before it manages to paint. Lower than 100ms
# starts to load the (modest) CPU on the Slate noticeably.
_REFRESH_INTERVAL_US = 120_000
RAW_PATH_ON_SLATE = "/tmp/.slate_fb_takeover.raw"


async def display_image_on_fb(
    ssh: SlateSSH,
    png_bytes: bytes,
    *,
    duration_seconds: float = 4.0,
    restart_gl_screen: bool = True,
) -> FbTakeoverReport:
    """Display `png_bytes` on the Slate's screen for `duration_seconds`.

    Implementation: upload raw RGB565 to /tmp on the Slate, then run a tight
    shell loop ON the Slate that pkill's any respawn of gl_screen and
    re-writes /dev/fb0 every ~120 ms. The loop runs locally, no per-frame
    SSH overhead — so even if procd respawns gl_screen aggressively, the
    new process is killed before it can paint a frame.

    Restart-after=False leaves the daemon stopped (caller does the restart),
    avoiding double-flicker when chained with the wallpaper applier.
    """
    rep = FbTakeoverReport()

    try:
        raw = _png_to_rgb565_portrait(png_bytes)
    except Exception as exc:  # noqa: BLE001
        rep.ok = False
        rep.errors.append(f"PNG → RGB565 failed: {exc}")
        return rep
    rep.steps.append(f"converted {len(png_bytes)}B PNG → {len(raw)}B raw RGB565")

    # 1. Upload the raw bytes ONCE to a temp file on the Slate. The local
    #    loop will `cat` this file → /dev/fb0 repeatedly.
    try:
        await ssh.put_bytes_raw(raw, RAW_PATH_ON_SLATE)
        rep.steps.append(f"uploaded raw to {RAW_PATH_ON_SLATE}")
    except SlateSSHError as exc:
        rep.ok = False
        rep.errors.append(f"upload raw: {exc}")
        return rep

    # 2. Stop via procd up-front. We rely on the local loop's pkill to
    #    keep it down for the duration.
    try:
        await ssh.run("/etc/init.d/gl_screen stop", timeout=10)
        rep.steps.append("gl_screen stopped (procd)")
    except SlateSSHError as exc:
        rep.ok = False
        rep.errors.append(f"stop gl_screen: {exc}")
        return rep

    # 3. Tight refresh loop ON the Slate. Runs synchronously over SSH for
    #    duration_seconds — we await its completion. Busybox-compatible:
    #    no bashisms, uses `date +%s` for the deadline and `usleep` for
    #    sub-second wait.
    #
    #    Each iteration: pkill any respawned gl_screen, then write raw fb.
    #    Both run unconditionally so we don't care about exit codes.
    dur = max(1, int(duration_seconds))
    # NOTE: busybox on this Slate has `killall` but NOT `pkill` — using
    # pkill silently does nothing and gl_screen wins the race. Verified
    # via `busybox --list`.
    loop_script = (
        "end=$(( $(date +%s) + " + str(dur) + " )); "
        "while [ $(date +%s) -lt $end ]; do "
        "  killall -9 gl_screen 2>/dev/null; "
        "  cat " + RAW_PATH_ON_SLATE + " > /dev/fb0 2>/dev/null; "
        "  usleep " + str(_REFRESH_INTERVAL_US) + "; "
        "done; "
        "rm -f " + RAW_PATH_ON_SLATE
    )
    try:
        await ssh.run(loop_script, timeout=dur + 10)
        rep.steps.append(
            f"hold loop ran on Slate for {dur}s "
            f"(refresh ~{_REFRESH_INTERVAL_US//1000}ms, ~{dur*1000//(_REFRESH_INTERVAL_US//1000)} frames)"
        )
    except SlateSSHError as exc:
        rep.ok = False
        rep.errors.append(f"hold loop: {exc}")

    # 4. Final restart (or leave stopped for the caller to handle).
    if restart_gl_screen:
        try:
            await ssh.run("/etc/init.d/gl_screen start", timeout=10)
            rep.steps.append("gl_screen restarted")
        except SlateSSHError as exc:
            rep.ok = False
            rep.errors.append(f"restart gl_screen: {exc}")
    else:
        rep.steps.append("(left gl_screen stopped — caller will restart)")

    logger.info(
        "slate.fb_takeover.done",
        bytes_png=len(png_bytes), duration=duration_seconds,
        restart=restart_gl_screen, ok=rep.ok,
    )
    return rep
