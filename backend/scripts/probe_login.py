"""Interactive credential probe (no .env, no caching).

Prompts for password each run; tries the given username then a fallback.
Usage:
    .venv/bin/python -m scripts.probe_login
"""

from __future__ import annotations

import getpass
import shutil
import sys
from pathlib import Path

from pyglinet import GlInet  # type: ignore[import-untyped]

URL = "https://192.168.8.1/rpc"
CACHE = Path.home() / ".python-glinet"


def _wipe_cache() -> None:
    if CACHE.exists():
        shutil.rmtree(CACHE)


def _try(username: str, password: str) -> bool:
    print(f"  → trying username={username!r} ...", end=" ", flush=True)
    _wipe_cache()  # paranoia: pyglinet caches creds across runs
    try:
        gl = GlInet(
            url=URL,
            username=username,
            password=password,
            verify_ssl_certificate=False,
            keep_alive=False,  # one-shot probe, no background thread
        )
        gl.login()
        print("✓ OK")
        try:
            gl.logout()
        except Exception:
            pass
        return True
    except Exception as exc:
        print(f"✗ {exc}")
        return False


def main() -> int:
    print(f"Probing {URL}")
    password = getpass.getpass("Slate password (won't echo): ")
    print(f"  (entered {len(password)} characters)")

    for username in ("root", "admin"):
        if _try(username, password):
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
