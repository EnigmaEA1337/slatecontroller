"""Probe what UCI access patterns work on this firmware.

We're trying to wire the hardening gauge checks (admin password, SSH, UPnP,
…) and don't yet know which RPC names the Slate exposes for UCI reads.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.config import get_settings
from app.exceptions import SlateError
from app.slate.client import SlateClient

# (group, method, params, description)
ATTEMPTS = [
    ("uci", "get_all", {"config": "upnpd"}, "uci.get_all upnpd"),
    ("uci", "get_all", {"config": "dropbear"}, "uci.get_all dropbear"),
    ("uci", "get_all", {"config": "uhttpd"}, "uci.get_all uhttpd"),
    ("uci", "get_all", {"config": "glconfig"}, "uci.get_all glconfig"),
    ("uci", "get_all", {"config": "firewall"}, "uci.get_all firewall (long)"),
    (
        "uci",
        "get",
        {"config": "glconfig", "section": "general", "option": "password_set"},
        "uci.get glconfig.general.password_set",
    ),
    (
        "uci",
        "get",
        {"config": "upnpd", "section": "config", "option": "enabled"},
        "uci.get upnpd.config.enabled",
    ),
    ("system", "uci_get_all", {"config": "upnpd"}, "system.uci_get_all upnpd"),
    ("uci", "show", {"config": "upnpd"}, "uci.show upnpd"),
    ("system", "get_glconfig", None, "system.get_glconfig"),
    ("glconfig", "get_config", None, "glconfig.get_config"),
]


def _truncate(s: str, max_len: int = 200) -> str:
    return s if len(s) <= max_len else s[:max_len] + f"... [+{len(s) - max_len} chars]"


async def main() -> int:
    settings = get_settings()
    client = SlateClient(
        url=settings.slate_url,
        username=settings.slate_username,
        password=settings.slate_password,
    )
    try:
        await client.connect()
    except SlateError as exc:
        print(f"✗ Slate unreachable: {exc}", file=sys.stderr)
        return 1
    print("✓ Connected.\n")

    for group, method, params, label in ATTEMPTS:
        try:
            res = await client.call(group, method, params)
            try:
                pretty = json.dumps(dict(res), default=str)
            except (TypeError, ValueError):
                pretty = str(res)
            print(f"  ✓ {label}")
            print(f"      {_truncate(pretty)}")
        except SlateError as exc:
            print(f"  ✗ {label}  →  {exc}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
