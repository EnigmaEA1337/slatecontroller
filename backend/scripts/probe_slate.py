"""Probe a live Slate via `SlateClient`.

Usage (from backend/ with venv active):

    SLATE_URL=https://192.168.8.1 \\
    SLATE_USERNAME=root \\
    SLATE_PASSWORD='your-password' \\
    .venv/bin/python -m scripts.probe_slate

Or fill `.env` at the repo root and just run:

    .venv/bin/python -m scripts.probe_slate

This is a development helper. It connects to the Slate, probes a list of
candidate JSON-RPC endpoints, and prints the result of each so you can
discover which ones your firmware actually exposes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from app.config import get_settings
from app.exceptions import SlateError
from app.slate.client import SlateClient

# (group, method) candidates - the ones the MVP cares about, plus a few
# extras for discovery. Edit freely while iterating.
CANDIDATE_CALLS: list[tuple[str, str]] = [
    ("system", "get_info"),
    ("system", "get_status"),
    ("system", "get_load"),
    ("system", "get_mem_info"),
    ("wan", "get_status"),
    ("clients", "get_list"),
    ("client", "get_list"),
    ("adguardhome", "get_status"),
    ("adguardhome", "get_config"),
    ("tailscale", "get_status"),
    ("wireguard_client", "get_status"),
    ("wg_client", "get_status"),
    ("openvpn_client", "get_status"),
    ("tor", "get_config"),
]


def _to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of pyglinet `ResultContainer` to JSON-friendly."""
    if obj is None or isinstance(obj, (str, int, float, bool, list, dict)):
        return obj
    for attr in ("data", "result", "to_dict"):
        if hasattr(obj, attr):
            value = getattr(obj, attr)
            value = value() if callable(value) else value
            if value is not obj:
                return _to_jsonable(value)
    try:
        return dict(obj)
    except (TypeError, ValueError):
        return str(obj)


def _truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated, full length = {len(text)}]"


async def main() -> int:
    settings = get_settings()
    print(f"→ Slate URL : {settings.slate_url}")
    print(f"→ Username  : {settings.slate_username}")
    pw_len = len(settings.slate_password)
    print(f"→ Password  : {'*' * min(pw_len, 8)} (len={pw_len})")

    if pw_len == 0 or settings.slate_password == "changeme":
        print("\n✗ SLATE_PASSWORD looks unset/default. Set it via env or .env.", file=sys.stderr)
        return 2

    client = SlateClient(
        url=settings.slate_url,
        username=settings.slate_username,
        password=settings.slate_password,
    )

    try:
        await client.connect()
    except SlateError as exc:
        print(f"\n✗ Connection failed: {exc}", file=sys.stderr)
        return 1
    print("\n✓ Connected.")

    print("\nProbing candidate RPC endpoints:")
    found: list[str] = []
    for group, method in CANDIDATE_CALLS:
        label = f"{group}.{method}"
        try:
            raw = await client.call(group, method)
            payload = _to_jsonable(raw)
            try:
                pretty = json.dumps(payload, indent=2, default=str)
            except (TypeError, ValueError):
                pretty = str(payload)
            print(f"\n  ✓ {label}")
            for line in _truncate(pretty).splitlines():
                print(f"      {line}")
            found.append(label)
        except SlateError as exc:
            print(f"  ✗ {label}  →  {exc}")

    print(f"\nSummary: {len(found)}/{len(CANDIDATE_CALLS)} endpoints OK")
    for label in found:
        print(f"  - {label}")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
