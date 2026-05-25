"""Load the cyberpunk SSID name suggestion library from YAML.

The library is read once at startup and cached. The file lives at
`backend/data/ssid_suggestions.yaml` so non-coders can append names without
touching Python.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

from app.config import BACKEND_DIR

logger = structlog.get_logger(__name__)

DEFAULT_SUGGESTIONS_PATH = BACKEND_DIR / "data" / "ssid_suggestions.yaml"


class SsidOption(BaseModel):
    name: str = Field(description="Broadcasted SSID name, e.g. 'BLACK_ICE'.")
    universe: str = Field(description="Universe tag, e.g. 'cyberpunk_2077'.")


class CategoryOptions(BaseModel):
    label: str
    description: str = ""
    icon: str | None = None
    options: list[SsidOption] = Field(default_factory=list)


class UniverseCombo(BaseModel):
    id: str
    label: str
    description: str = ""
    ssids: dict[str, str] = Field(
        description="Mapping category → SSID name (the 5-name cohesive set).",
    )


class SsidSuggestionsLibrary(BaseModel):
    categories: dict[str, CategoryOptions] = Field(default_factory=dict)
    universe_combos: list[UniverseCombo] = Field(default_factory=list)

    @property
    def universes(self) -> list[str]:
        """All distinct universe tags found across categories — for UI filtering."""
        tags: set[str] = set()
        for cat in self.categories.values():
            for opt in cat.options:
                tags.add(opt.universe)
        return sorted(tags)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        logger.warning("ssid_suggestions.missing", path=str(path))
        return {"categories": {}, "universe_combos": []}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return raw


@lru_cache(maxsize=1)
def get_suggestions_library() -> SsidSuggestionsLibrary:
    """Read + validate the YAML. Cached for the process lifetime."""
    raw = _load_yaml(DEFAULT_SUGGESTIONS_PATH)
    lib = SsidSuggestionsLibrary.model_validate(raw)
    logger.info(
        "ssid_suggestions.loaded",
        categories=len(lib.categories),
        combos=len(lib.universe_combos),
        universes=len(lib.universes),
    )
    return lib


def reload_suggestions() -> SsidSuggestionsLibrary:
    """Bust the cache (intended for dev / hot-edit of the YAML)."""
    get_suggestions_library.cache_clear()
    return get_suggestions_library()
