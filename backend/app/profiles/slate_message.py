"""Public API for displaying any message on the Slate's front screen.

Wraps two layers (PNG render + framebuffer takeover) into a single clean
call. Use this — not the low-level fb_takeover.display_image_on_fb — for
arbitrary status messages from anywhere in the backend.

Architecture (kept short — the gory details live in the two modules below):
  app.profiles.status_screen   →  PIL renders a 320×240 cyber PNG
  app.profiles.fb_takeover     →  stops gl_screen, writes raw RGB565 to
                                  /dev/fb0, holds N seconds, (optional restart)
  THIS module                  →  composes the two with a friendly signature

Use cases:
  - Profile activation: `display_message(ssh, title="MISE A JOUR",
        subtitle="depuis Slate Controller", target="MISSION",
        restart_after=False)` — caller will restart after pushing wallpapers
  - Error reporting: `display_message(ssh, title="ERREUR",
        subtitle="Tailscale down", duration_seconds=5)`
  - Generic notification on a side channel.
"""

from __future__ import annotations

from app.profiles.fb_takeover import FbTakeoverReport, display_image_on_fb
from app.profiles.status_screen import render_status_image
from app.slate.ssh import SlateSSH


async def display_message(
    ssh: SlateSSH,
    *,
    title: str = "MISE A JOUR",
    subtitle: str = "depuis Slate Controller",
    target: str | None = None,
    kind: str = "status",  # "status" | "action" | "error" | "ok"
    duration_seconds: float = 4.0,
    restart_after: bool = True,
) -> FbTakeoverReport:
    """Display a cyber-themed message full-screen on the Slate.

    Args:
        ssh: live SSH connection to the Slate.
        title: big white text (e.g. "MISE A JOUR").
        subtitle: smaller line under the title.
        target: optional, displayed in a bottom-left status pill.
        kind: semantic colour — "status"/"action"/"error" use the red
            accent, "ok" uses green. Drives the label `// STATUS` etc.
        duration_seconds: how long the image stays on the panel.
        restart_after: when False, the caller will restart gl_screen later
            (chained flows). When True, restart at end for standalone use.
    """
    png = await render_status_image(
        ssh, title=title, subtitle=subtitle, target=target, kind=kind,  # type: ignore[arg-type]
    )
    return await display_image_on_fb(
        ssh,
        png,
        duration_seconds=duration_seconds,
        restart_gl_screen=restart_after,
    )
