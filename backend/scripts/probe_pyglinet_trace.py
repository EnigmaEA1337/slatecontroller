"""Trace exactly what pyglinet POSTs by intercepting `requests.Session.request`.

Reveals the on-the-wire payload differences vs our manual probe.

Usage:
    .venv/bin/python -m scripts.probe_pyglinet_trace
"""

from __future__ import annotations

import getpass
import json
import shutil
from pathlib import Path

import requests
from pyglinet import GlInet  # type: ignore[import-untyped]


def main() -> int:
    cache = Path.home() / ".python-glinet"
    if cache.exists():
        shutil.rmtree(cache)

    password = getpass.getpass("password (no echo): ")

    original = requests.Session.request

    def traced(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        body = kwargs.get("json") or kwargs.get("data")
        print(f"\n→ {method} {url}")
        if body is not None:
            print(f"  body: {json.dumps(body, indent=2) if isinstance(body, dict) else body}")
        resp = original(self, method, url, *args, **kwargs)
        try:
            print(f"← {resp.status_code} {resp.json()}")
        except Exception:
            print(f"← {resp.status_code} {resp.text[:200]}")
        return resp

    requests.Session.request = traced  # type: ignore[method-assign]

    gl = GlInet(
        url="https://192.168.8.1/rpc",
        username="root",
        password=password,
        verify_ssl_certificate=False,
        keep_alive=False,
    )
    try:
        gl.login()
        print("\n✓ pyglinet login OK")
        return 0
    except Exception as exc:
        print(f"\n✗ pyglinet login failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
