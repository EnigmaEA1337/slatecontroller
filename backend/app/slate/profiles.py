"""ProfileManager: load YAML profile definitions from disk."""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml
from pydantic import ValidationError

from app.models.profile import Profile

logger = structlog.get_logger(__name__)


class ProfileLoadError(Exception):
    """Raised when a profile YAML is invalid or unparseable."""

    def __init__(self, file: Path, message: str) -> None:
        super().__init__(f"{file.name}: {message}")
        self.file = file


class ProfileManager:
    """Read-only loader for profile YAMLs.

    Profiles are cached in-memory after the first read. Call `reload()` to
    re-read from disk (intended for future hot-reload or CRUD endpoints).
    """

    def __init__(self, profiles_dir: str | Path) -> None:
        self._dir = Path(profiles_dir)
        self._cache: dict[str, Profile] | None = None

    @property
    def directory(self) -> Path:
        return self._dir

    def _read(self) -> dict[str, Profile]:
        if not self._dir.is_dir():
            logger.warning("profiles.dir_missing", path=str(self._dir))
            return {}

        loaded: dict[str, Profile] = {}
        for yaml_file in sorted(self._dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ProfileLoadError(yaml_file, f"YAML parse error: {exc}") from exc
            if not isinstance(raw, dict):
                raise ProfileLoadError(yaml_file, "top-level YAML must be a mapping")
            try:
                profile = Profile.model_validate(raw)
            except ValidationError as exc:
                raise ProfileLoadError(yaml_file, f"schema validation failed: {exc}") from exc
            if profile.name in loaded:
                raise ProfileLoadError(
                    yaml_file, f"duplicate profile name {profile.name!r}"
                )
            loaded[profile.name] = profile
        logger.info("profiles.loaded", count=len(loaded), dir=str(self._dir))
        return loaded

    def list_all(self) -> list[Profile]:
        if self._cache is None:
            self._cache = self._read()
        return list(self._cache.values())

    def get(self, name: str) -> Profile | None:
        if self._cache is None:
            self._cache = self._read()
        return self._cache.get(name)

    def reload(self) -> None:
        """Drop the in-memory cache; next access will re-read from disk."""
        self._cache = None
