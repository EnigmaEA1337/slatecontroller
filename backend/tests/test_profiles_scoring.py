"""Tests for the profile scoring module."""

from __future__ import annotations

from app.models.profile import Profile
from app.profiles.scoring import compute_scores


def _make(**kwargs: object) -> Profile:
    """Build a Profile with sensible 'nothing on' defaults, override via kwargs.

    Legacy `adguard:` / `dns:` keys passed via kwargs are silently
    dropped by `_drop_legacy_keys` on the Profile model — these blocks
    were removed when filtering / DNS moved to per-network controls.
    Tests keep passing them only as a documented no-op, so the kwargs
    serve as readable test intent.
    """
    base: dict = {
        "name": "test",
        "description": "",
        "vpn": {"type": "none", "client": None, "kill_switch": False},
        "tor": {"enabled": False, "bridge": False},
        "tailscale": {"enabled": False, "admin_only": False},
        "ssids": [],
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
        firewall={
            "lockdown": True,
            "geoip_whitelist": ["FR"],
            "block_telemetry": True,
            "block_all_outbound": True,
        },
        logging={"level": "DEBUG", "forward_to_siem": True},
    )
    scores = compute_scores(p)
    # Threshold relaxed (was 90) since AdGuard-activé (10 pts) was retired
    # — DNS protection moved to per-network and is no longer profile-scored.
    assert scores.security >= 80


def test_osint_like_profile_is_high_anonymization() -> None:
    """Mimics our osint template — VPN+Tor+private DNS = high anon."""
    p = _make(
        vpn={"type": "wireguard", "client": "mullvad", "kill_switch": True},
        tor={"enabled": True, "bridge": False},
        firewall={
            "lockdown": True,
            "geoip_whitelist": [],
            "block_telemetry": True,
            "block_all_outbound": False,
        },
    )
    scores = compute_scores(p)
    # 25 (vpn) + 10 (kill) + 35 (tor) + 5 (telemetry) = 75. AdGuard tif
    # and private-DNS items were retired (per-network now), hence the
    # lower expectation.
    assert scores.anonymization >= 70


def test_home_profile_comfort_first() -> None:
    """Mimics our home template — comfort over hardening.

    No VPN, no Tor, no firewall lockdown → 0 anonymization is correct.
    Security gets only a small boost from SIEM logging (AdGuard scoring
    is no longer profile-driven).
    """
    p = _make(
        vpn={"type": "none", "client": None, "kill_switch": False},
        logging={"level": "INFO", "forward_to_siem": True},
    )
    scores = compute_scores(p)
    assert scores.anonymization == 0  # nothing hides the user
    assert 5 <= scores.security <= 30  # SIEM logging only


def test_breakdown_items_have_max_points() -> None:
    """Every score item should declare a non-zero max_points.

    The exact item count isn't asserted — the breakdown is intentionally
    free to grow as new posture signals are added. We just check that
    the structure is well-formed for an empty profile (all zeros, but
    every item carries its max).
    """
    scores = compute_scores(_make())
    assert len(scores.breakdown_anonymization) >= 5
    assert len(scores.breakdown_security) >= 7
    for item in scores.breakdown_anonymization + scores.breakdown_security:
        assert item.points == 0
        assert item.max_points > 0


def test_maxed_profile_hits_max_breakdown_sum() -> None:
    """An over-configured profile maxes every item it's eligible for.

    We don't assert ``score == 100`` anymore — the breakdown max moves
    when items are added/removed (e.g. when AdGuard scoring was retired
    after DNS protection moved to per-network). Asserting against the
    *actual* max_points sum is more robust.
    """
    p = _make(
        vpn={"type": "wireguard", "client": "x", "kill_switch": True},
        tor={"enabled": True, "bridge": True},
        firewall={
            "lockdown": True,
            "geoip_whitelist": ["FR", "CH"],
            "block_telemetry": True,
            "block_all_outbound": True,
        },
        logging={"level": "INFO", "forward_to_siem": True},
    )
    scores = compute_scores(p)
    anon_max = sum(i.max_points for i in scores.breakdown_anonymization)
    sec_max = sum(i.max_points for i in scores.breakdown_security)
    # Some security items are wifi-derived ; an empty SSID list scores 0
    # on those, so we deduct them before asserting "everything else maxed".
    wifi_zero = sum(
        i.max_points for i in scores.breakdown_security
        if i.name in {"WPA3 sur SSIDs activés", "Robustesse des PSK", "Aucun SSID 'open'"}
    )
    assert scores.anonymization == anon_max
    assert scores.security == sec_max - wifi_zero
