"""Tests for the profile scoring module."""

from __future__ import annotations

from app.models.profile import Profile
from app.profiles.scoring import compute_scores


def _make(**kwargs: object) -> Profile:
    """Build a Profile with sensible 'nothing on' defaults, override via kwargs."""
    base: dict = {
        "name": "test",
        "description": "",
        "vpn": {"type": "none", "client": None, "kill_switch": False},
        "tor": {"enabled": False, "bridge": False},
        "tailscale": {"enabled": False, "admin_only": False},
        "adguard": {"enabled": False, "lists": []},
        "ssids": [],
        "dns": {"servers": [], "forced": False},
        "firewall": {
            "lockdown": False,
            "geoip_whitelist": [],
            "block_telemetry": False,
            "block_all_outbound": False,
        },
        "logging": {"level": "INFO", "forward_to_siem": False},
    }
    base.update(kwargs)  # type: ignore[arg-type]
    return Profile.model_validate(base)


def test_empty_profile_scores_zero() -> None:
    scores = compute_scores(_make())
    assert scores.anonymization == 0
    assert scores.security == 0


def test_lockdown_like_profile_is_high_security() -> None:
    """Mimics our lockdown template — should score very high on security."""
    p = _make(
        vpn={"type": "wireguard", "client": "mullvad", "kill_switch": True},
        adguard={"enabled": True, "lists": ["hagezi-pro-plus"]},
        dns={"servers": ["9.9.9.9"], "forced": True},
        firewall={
            "lockdown": True,
            "geoip_whitelist": ["FR"],
            "block_telemetry": True,
            "block_all_outbound": True,
        },
        logging={"level": "DEBUG", "forward_to_siem": True},
    )
    scores = compute_scores(p)
    assert scores.security >= 90  # most security items checked


def test_osint_like_profile_is_high_anonymization() -> None:
    """Mimics our osint template — VPN+Tor+private DNS = high anon."""
    p = _make(
        vpn={"type": "wireguard", "client": "mullvad", "kill_switch": True},
        tor={"enabled": True, "bridge": False},
        adguard={"enabled": True, "lists": ["hagezi-tif"]},
        dns={"servers": ["127.0.0.1:5353"], "forced": True},
        firewall={
            "lockdown": True,
            "geoip_whitelist": [],
            "block_telemetry": True,
            "block_all_outbound": False,
        },
    )
    scores = compute_scores(p)
    assert scores.anonymization >= 85
    # 25 (vpn) + 10 (kill) + 35 (tor) + 10 (private dns) + 5 (adguard tif) + 5 (telemetry)
    # = 90


def test_home_profile_comfort_first() -> None:
    """Mimics our home template — comfort over hardening.

    No VPN, no Tor, public DNS, no firewall lockdown → 0 anonymization is
    correct. Security gets a small boost from AdGuard + SIEM logging.
    """
    p = _make(
        vpn={"type": "none", "client": None, "kill_switch": False},
        adguard={"enabled": True, "lists": ["oisd-big"]},
        dns={"servers": ["1.1.1.1", "9.9.9.9"], "forced": False},
        logging={"level": "INFO", "forward_to_siem": True},
    )
    scores = compute_scores(p)
    assert scores.anonymization == 0  # nothing hides the user
    assert 10 <= scores.security <= 40  # AdGuard + SIEM only


def test_breakdown_has_one_item_per_criterion() -> None:
    scores = compute_scores(_make())
    # Anonymization: 8 criteria currently
    assert len(scores.breakdown_anonymization) == 8
    # Security: 8 criteria
    assert len(scores.breakdown_security) == 8
    # All items in an empty profile should have 0 points but max_points set.
    for item in scores.breakdown_anonymization + scores.breakdown_security:
        assert item.points == 0
        assert item.max_points > 0


def test_score_caps_at_100() -> None:
    """Even an over-configured profile shouldn't exceed 100."""
    p = _make(
        vpn={"type": "wireguard", "client": "x", "kill_switch": True},
        tor={"enabled": True, "bridge": True},
        adguard={"enabled": True, "lists": ["hagezi-pro-plus", "tif"]},
        dns={"servers": ["10.0.0.1"], "forced": True},
        firewall={
            "lockdown": True,
            "geoip_whitelist": ["FR", "CH"],
            "block_telemetry": True,
            "block_all_outbound": True,
        },
        logging={"level": "INFO", "forward_to_siem": True},
    )
    scores = compute_scores(p)
    assert scores.anonymization == 100
    assert scores.security == 100
