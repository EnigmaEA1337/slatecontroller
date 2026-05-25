"""Cached fetch of TTF fonts from the Slate.

The backend Docker image is slim and ships without TTF fonts, but the
Slate itself carries proper TTFs under /etc/gl_screen/language/ttf/.
We fetch them once via SSH on first use and cache on the controller's
persistent volume — subsequent renders use the cached copy directly.

This sidesteps having to bundle external fonts in the project repo or
add `fonts-*` packages to the Dockerfile (which would need a rebuild).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import structlog

from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

CACHE_DIR = Path("/app/data/cache/fonts")
SLATE_FONT_DIR = "/etc/gl_screen/language/ttf"
# Aliases the OEM uses (see /etc/gl_screen/language/text/default).
FONT_NAMES = ("default_medium", "default_bold", "default_semibold", "default_mono_medium")

# Single in-process lock so concurrent requests don't double-fetch.
_fetch_lock = asyncio.Lock()


def _local_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.ttf"


async def fetch_font(ssh: SlateSSH, name: str = "default_medium") -> Path:
    """Return the local path to the named TTF font, fetching once if missing."""
    if name not in FONT_NAMES:
        raise ValueError(f"font {name!r} not in known set {FONT_NAMES}")
    local = _local_path(name)
    if local.exists() and local.stat().st_size > 0:
        return local
    async with _fetch_lock:
        # Re-check inside the lock to avoid racing.
        if local.exists() and local.stat().st_size > 0:
            return local
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        remote = f"{SLATE_FONT_DIR}/{name}.ttf"
        logger.info("font_cache.fetch", name=name, remote=remote)
        try:
            data = await ssh.run_binary(f"cat {remote}", timeout=15.0)
        except SlateSSHError as exc:
            raise RuntimeError(f"failed to fetch font {name!r} from Slate: {exc}") from exc
        if not data:
            raise RuntimeError(f"font {name!r} fetched zero bytes")
        # Atomic write: tmp + rename.
        tmp = local.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, local)
        logger.info("font_cache.cached", name=name, bytes=len(data), path=str(local))
        return local
