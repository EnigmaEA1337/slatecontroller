"""Show methods inside specific RPC namespaces."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    import pyglinet  # type: ignore[import-untyped]

    pkg_dir = Path(pyglinet.__file__).resolve().parent
    desc = json.loads((pkg_dir / "api" / "api_description.json").read_text(encoding="utf-8"))

    focus = [
        "firewall",
        "system",
        "acl",
        "ui",
        "upgrade",
        "wg_client",
        "ovpn_client",
        "ovpn_server",
        "wifi",
        "ipv6",
        "vpn_policy",
        "dns",
        "lan",
        "network",
    ]
    for group_name in focus:
        node = desc.get(group_name)
        if not isinstance(node, dict):
            continue
        # The dict typically has method names as keys with metadata as values.
        methods = sorted(node.keys())
        print(f"\n=== {group_name}  ({len(methods)} methods) ===")
        for m in methods:
            print(f"  {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
