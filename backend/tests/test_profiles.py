"""Tests for ProfileManager (YAML seed loader only).

Route/CRUD tests live in `test_profiles_routes.py` (DB-backed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.profile import Profile
from app.slate.profiles import ProfileLoadError, ProfileManager

REAL_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
EXPECTED_NAMES = {"mission", "vacances", "osint", "home", "lockdown"}


def test_manager_loads_all_real_profiles() -> None:
    """Smoke: the 5 shipped YAMLs all validate against the Profile schema."""
    manager = ProfileManager(REAL_PROFILES_DIR)
    profiles = manager.list_all()
    names = {p.name for p in profiles}
    assert names == EXPECTED_NAMES


def test_manager_get_returns_typed_profile() -> None:
    manager = ProfileManager(REAL_PROFILES_DIR)
    mission = manager.get("mission")
    assert isinstance(mission, Profile)
    assert mission.vpn.kill_switch is True
    assert mission.firewall.lockdown is True
    assert "FR" in mission.firewall.geoip_whitelist


def test_manager_get_returns_none_for_missing() -> None:
    manager = ProfileManager(REAL_PROFILES_DIR)
    assert manager.get("does-not-exist") is None


def test_manager_missing_dir_returns_empty(tmp_path: Path) -> None:
    manager = ProfileManager(tmp_path / "does-not-exist")
    assert manager.list_all() == []


def test_manager_rejects_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("name: test\n  bad-indent: x", encoding="utf-8")
    manager = ProfileManager(tmp_path)
    with pytest.raises(ProfileLoadError) as exc_info:
        manager.list_all()
    assert "broken.yaml" in str(exc_info.value)


def test_manager_rejects_schema_violation(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("description: missing name\n", encoding="utf-8")
    manager = ProfileManager(tmp_path)
    with pytest.raises(ProfileLoadError) as exc_info:
        manager.list_all()
    assert "schema validation failed" in str(exc_info.value)


def test_manager_rejects_unknown_keys(tmp_path: Path) -> None:
    """Typos in profile YAMLs must fail loudly, not silently drop."""
    (tmp_path / "typo.yaml").write_text(
        "name: typo\nfirewal: {}  # typo: should be `firewall`\n",
        encoding="utf-8",
    )
    manager = ProfileManager(tmp_path)
    with pytest.raises(ProfileLoadError):
        manager.list_all()


def test_manager_rejects_duplicate_names(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("name: dup\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("name: dup\n", encoding="utf-8")
    manager = ProfileManager(tmp_path)
    with pytest.raises(ProfileLoadError) as exc_info:
        manager.list_all()
    assert "duplicate" in str(exc_info.value)


def test_manager_reload_clears_cache(tmp_path: Path) -> None:
    yaml_file = tmp_path / "p.yaml"
    yaml_file.write_text("name: one\n", encoding="utf-8")
    manager = ProfileManager(tmp_path)
    assert [p.name for p in manager.list_all()] == ["one"]

    yaml_file.write_text("name: two\n", encoding="utf-8")
    assert [p.name for p in manager.list_all()] == ["one"]  # cached

    manager.reload()
    assert [p.name for p in manager.list_all()] == ["two"]
