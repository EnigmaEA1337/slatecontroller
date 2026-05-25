"""List every RPC group + method the Slate exposes via pyglinet's discovery.

pyglinet ships an api_description.json built into the package that mirrors
GL.iNet's namespace tree. Walking it gives us the entire surface — much
faster than guessing endpoint names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    import pyglinet  # type: ignore[import-untyped]

    pkg_dir = Path(pyglinet.__file__).resolve().parent
    api_desc = pkg_dir / "api" / "api_description.json"
    if not api_desc.is_file():
        print(f"✗ no api_description.json at {api_desc}", file=sys.stderr)
        return 1

    desc = json.loads(api_desc.read_text(encoding="utf-8"))
    # The structure: typically a dict mapping group → list of method dicts.
    if isinstance(desc, dict):
        for group_name in sorted(desc.keys()):
            methods = desc[group_name]
            if isinstance(methods, list):
                names = sorted(
                    m.get("name", "?") if isinstance(m, dict) else str(m)
                    for m in methods
                )
                print(f"{group_name}  ({len(names)} methods)")
                for n in names:
                    print(f"  - {n}")
            else:
                print(f"{group_name}  (non-list: {type(methods).__name__})")
    else:
        print(f"top-level type: {type(desc).__name__}")
        print(json.dumps(desc, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
