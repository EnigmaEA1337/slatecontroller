"""Profile scoring — anonymization and security percentages.

Two independent scores computed from a Profile's configuration. Both range
0-100. The breakdown lets the UI explain *why* a profile scored as it did
(which criteria contributed how many points).

The thresholds are deliberately opinionated; revisit if your threat model
differs. For example, "block_all_outbound" weighs heavily in security
because it's a default-deny posture, but it's also operationally painful.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.profile import Profile
from app.wifi.models import WifiSecurity



@dataclass
class ScoreItem:
    name: str
    points: int
    max_points: int
    note: str = ""


@dataclass
class ProfileScores:
    anonymization: int
    security: int
    breakdown_anonymization: list[ScoreItem] = field(default_factory=list)
    breakdown_security: list[ScoreItem] = field(default_factory=list)


# ---------------------------- Anonymization ---------------------------- #


def _score_anonymization(profile: Profile) -> tuple[int, list[ScoreItem]]:
    items: list[ScoreItem] = []

    vpn_on = profile.vpn.type != "none"
    items.append(
        ScoreItem(
            name="VPN actif",
            points=25 if vpn_on else 0,
            max_points=25,
            note=f"type={profile.vpn.type}" if vpn_on else "aucun VPN",
        )
    )
    items.append(
        ScoreItem(
            name="Kill-switch",
            points=10 if profile.vpn.kill_switch else 0,
            max_points=10,
            note="empêche tout leak si le tunnel tombe" if profile.vpn.kill_switch else "",
        )
    )

    tor_on = profile.tor.enabled
    items.append(
        ScoreItem(
            name="Tor activé",
            points=35 if tor_on else 0,
            max_points=35,
            note="chaîne de 3 relais Tor" if tor_on else "",
        )
    )
    items.append(
        ScoreItem(
            name="Tor bridges",
            points=5 if (tor_on and profile.tor.bridge) else 0,
            max_points=5,
            note="résistance au DPI / pays bloquant Tor" if profile.tor.bridge else "",
        )
    )

    # DNS scoring removed from per-profile schema — protection is per-network
    # now and scored implicitly via the DnsProtectionManager presence.

    has_anti_tracking = profile.adguard.enabled and any(
        "tif" in s or "tracking" in s or "pro" in s.lower()
        for s in profile.adguard.lists
    )
    items.append(
        ScoreItem(
            name="AdGuard anti-tracking",
            points=5 if has_anti_tracking else 0,
            max_points=5,
            note="liste hagezi-tif / pro-plus / anti-tracking active"
            if has_anti_tracking
            else "",
        )
    )

    items.append(
        ScoreItem(
            name="Block telemetry firewall",
            points=5 if profile.firewall.block_telemetry else 0,
            max_points=5,
        )
    )
    items.append(
        ScoreItem(
            name="GeoIP whitelist",
            points=5 if profile.firewall.geoip_whitelist else 0,
            max_points=5,
            note=", ".join(profile.firewall.geoip_whitelist) or "",
        )
    )

    score = sum(item.points for item in items)
    return min(score, 100), items


# ---------------------------- Wi-Fi strength ---------------------------- #


def password_strength(pw: str) -> int:
    """Coarse strength score for a Wi-Fi PSK: 0 (very weak) → 3 (strong)."""
    if len(pw) < 8:
        return 0
    classes = sum(
        any(check(c) for c in pw)
        for check in (
            str.islower,
            str.isupper,
            str.isdigit,
            lambda c: not c.isalnum(),
        )
    )
    if len(pw) >= 16 and classes >= 3:
        return 3
    if len(pw) >= 12 and classes >= 2:
        return 2
    return 1


def _score_wifi_strength(
    profile: Profile,
    wifi_secrets: dict[str, tuple[WifiSecurity, str]],
) -> list[ScoreItem]:
    """Three criteria on the SSIDs activated by this profile.

    `wifi_secrets` maps slug → (security_mode, decrypted_password). Only
    enabled SSIDs are considered.
    """
    enabled_slugs = [ref.slug for ref in profile.ssids if ref.enabled]
    if not enabled_slugs:
        return [
            ScoreItem(name="WPA3 sur SSIDs activés", points=0, max_points=5, note="aucun SSID activé"),
            ScoreItem(name="Robustesse des PSK", points=0, max_points=5, note="aucun SSID activé"),
            ScoreItem(name="Aucun SSID 'open'", points=0, max_points=3, note="aucun SSID activé"),
        ]

    securities: list[WifiSecurity] = []
    passwords: list[str] = []
    for slug in enabled_slugs:
        secret = wifi_secrets.get(slug)
        if secret is None:
            continue
        sec, pw = secret
        securities.append(sec)
        passwords.append(pw)

    all_wpa3 = bool(securities) and all(s.startswith("WPA3") for s in securities)
    no_open = bool(securities) and not any(s == "open" for s in securities)
    strong_pw = bool(passwords) and all(password_strength(p) >= 2 for p in passwords)

    return [
        ScoreItem(
            name="WPA3 sur SSIDs activés",
            points=5 if all_wpa3 else 0,
            max_points=5,
            note=(
                f"{sum(s.startswith('WPA3') for s in securities)}/{len(securities)} en WPA3"
                if securities
                else "catalog Wi-Fi inaccessible"
            ),
        ),
        ScoreItem(
            name="Robustesse des PSK",
            points=5 if strong_pw else 0,
            max_points=5,
            note=(
                "tous ≥12 chars avec 2+ classes"
                if strong_pw and passwords
                else "au moins un PSK faible (<12 chars ou trop simple)"
                if passwords
                else "aucun PSK à évaluer"
            ),
        ),
        ScoreItem(
            name="Aucun SSID 'open'",
            points=3 if no_open else 0,
            max_points=3,
            note="tous chiffrés" if no_open else "au moins un SSID open",
        ),
    ]


# ---------------------------- Security ---------------------------- #


def _score_security(
    profile: Profile,
    wifi_secrets: dict[str, tuple[WifiSecurity, str]] | None = None,
) -> tuple[int, list[ScoreItem]]:
    items: list[ScoreItem] = []

    items.append(
        ScoreItem(
            name="Firewall lockdown",
            points=20 if profile.firewall.lockdown else 0,
            max_points=20,
            note="default-deny postures + whitelist explicite"
            if profile.firewall.lockdown
            else "",
        )
    )

    vpn_secure = profile.vpn.type != "none" and profile.vpn.kill_switch
    items.append(
        ScoreItem(
            name="VPN + kill-switch",
            points=15 if vpn_secure else 0,
            max_points=15,
            note="tunnel obligatoire, zéro leak si chute" if vpn_secure else "",
        )
    )

    # DNS forcing score removed — concept moved to per-network DNS protection.
    items.append(
        ScoreItem(
            name="AdGuard activé",
            points=10 if profile.adguard.enabled else 0,
            max_points=10,
            note="bloque malware + tracking au niveau DNS"
            if profile.adguard.enabled
            else "",
        )
    )
    items.append(
        ScoreItem(
            name="GeoIP whitelist",
            points=10 if profile.firewall.geoip_whitelist else 0,
            max_points=10,
            note="limite la surface d'attaque géographique"
            if profile.firewall.geoip_whitelist
            else "",
        )
    )
    items.append(
        ScoreItem(
            name="Block telemetry",
            points=5 if profile.firewall.block_telemetry else 0,
            max_points=5,
        )
    )
    items.append(
        ScoreItem(
            name="Block all outbound (paranoid)",
            points=20 if profile.firewall.block_all_outbound else 0,
            max_points=20,
            note="default-deny outbound + whitelist explicite uniquement"
            if profile.firewall.block_all_outbound
            else "",
        )
    )
    logging_ok = profile.logging.forward_to_siem and profile.logging.level in (
        "DEBUG",
        "INFO",
    )
    items.append(
        ScoreItem(
            name="Logging + SIEM forward",
            points=10 if logging_ok else 0,
            max_points=10,
            note=f"level={profile.logging.level}, SIEM={profile.logging.forward_to_siem}"
            if logging_ok
            else "audit trail manquant ou trop verbeux",
        )
    )

    # Optional: Wi-Fi-related checks (require decrypted PSKs).
    if wifi_secrets is not None:
        items.extend(_score_wifi_strength(profile, wifi_secrets))

    score = sum(item.points for item in items)
    return min(score, 100), items


def compute_scores(
    profile: Profile,
    *,
    wifi_secrets: dict[str, tuple[WifiSecurity, str]] | None = None,
) -> ProfileScores:
    """Compute both scores for a profile.

    `wifi_secrets`: optional mapping `slug → (security, decrypted_password)`
    for SSIDs referenced by the profile. When supplied, three additional
    criteria are added to the security breakdown (WPA3, PSK strength, no open).
    """
    anon_score, anon_items = _score_anonymization(profile)
    sec_score, sec_items = _score_security(profile, wifi_secrets=wifi_secrets)
    return ProfileScores(
        anonymization=anon_score,
        security=sec_score,
        breakdown_anonymization=anon_items,
        breakdown_security=sec_items,
    )
