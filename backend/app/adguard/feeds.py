"""Catalogue de blocklists DNS recommandées.

Liste statique — change rarement, pas besoin de DB. Référencée par les
profils YAML (`adguard.lists: [hagezi-pro, oisd-big]`) et exposée à l'UI
via `GET /api/adguard/feeds/catalog`.

Sources principalement HaGeZi (curated) + OISD + AdGuard officiel + classiques
(Steven Black, 1Hosts, Phishing Army).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FeedCategory = Literal["ads", "tracking", "malware", "phishing", "social", "nsfw", "all"]


@dataclass(frozen=True)
class FeedEntry:
    slug: str
    name: str
    description: str
    url: str
    category: FeedCategory
    maintainer: str
    # Coarse intensity hint for the UI: light = least false positives, hard = aggressive.
    intensity: Literal["light", "balanced", "pro", "hard"]
    # Whether this is included by default in shipped profiles.
    recommended: bool = False


CATALOG: list[FeedEntry] = [
    # ---------- HaGeZi family (the gold standard for curated lists) ----------
    FeedEntry(
        slug="hagezi-light",
        name="HaGeZi Light",
        description="Minimal blocklist, lowest false positives. Bon point de départ.",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/light.txt",
        category="ads",
        maintainer="HaGeZi",
        intensity="light",
    ),
    FeedEntry(
        slug="hagezi-multi",
        name="HaGeZi Multi Normal",
        description="Pubs + traqueurs + malware + phishing. Standard quotidien.",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/multi.txt",
        category="all",
        maintainer="HaGeZi",
        intensity="balanced",
        recommended=True,
    ),
    FeedEntry(
        slug="hagezi-pro",
        name="HaGeZi Multi PRO",
        description="Multi + plus de traqueurs aggressifs. Recommandé pour usage \"mission\".",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.txt",
        category="all",
        maintainer="HaGeZi",
        intensity="pro",
        recommended=True,
    ),
    FeedEntry(
        slug="hagezi-pro-plus",
        name="HaGeZi Multi PRO++",
        description="PRO + traqueurs hard, mining, fingerprinting. Risque faux positifs.",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.plus.txt",
        category="all",
        maintainer="HaGeZi",
        intensity="hard",
    ),
    FeedEntry(
        slug="hagezi-tif",
        name="HaGeZi Threat Intel Feeds",
        description="Domaines malware/phishing actifs (sources OSINT cumulées).",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.txt",
        category="malware",
        maintainer="HaGeZi",
        intensity="balanced",
        recommended=True,
    ),
    FeedEntry(
        slug="hagezi-tif-medium",
        name="HaGeZi TIF Medium",
        description="TIF allégée, moins agressive sur les domaines suspects.",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.medium.txt",
        category="malware",
        maintainer="HaGeZi",
        intensity="balanced",
    ),

    # ---------- OISD (Online Isolation Stop Domains) ----------
    FeedEntry(
        slug="oisd-small",
        name="OISD Small",
        description="Pubs + traqueurs basiques, très peu de faux positifs.",
        url="https://small.oisd.nl/",
        category="ads",
        maintainer="OISD",
        intensity="light",
    ),
    FeedEntry(
        slug="oisd-big",
        name="OISD Big",
        description="Liste complète OISD : pubs, traqueurs, malware, scam.",
        url="https://big.oisd.nl/",
        category="all",
        maintainer="OISD",
        intensity="balanced",
        recommended=True,
    ),
    FeedEntry(
        slug="oisd-nsfw",
        name="OISD NSFW",
        description="Bloque les domaines NSFW (porn, gambling). Utile pour profils enfants.",
        url="https://nsfw.oisd.nl/",
        category="nsfw",
        maintainer="OISD",
        intensity="balanced",
    ),

    # ---------- AdGuard officiel ----------
    FeedEntry(
        slug="adguard-base",
        name="AdGuard Base Filter",
        description="Filtre de base AdGuard (pubs/popups). Pré-installé sur le Slate.",
        url="https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt",
        category="ads",
        maintainer="AdGuard",
        intensity="balanced",
    ),
    FeedEntry(
        slug="adguard-tracking",
        name="AdGuard Tracking Protection",
        description="Filtre traqueurs AdGuard.",
        url="https://adguardteam.github.io/HostlistsRegistry/assets/filter_3.txt",
        category="tracking",
        maintainer="AdGuard",
        intensity="balanced",
    ),

    # ---------- Phishing / malware spécialisé ----------
    FeedEntry(
        slug="phishing-army",
        name="Phishing Army Extended",
        description="Domaines de phishing actifs (mis à jour très fréquemment).",
        url="https://phishing.army/download/phishing_army_blocklist_extended.txt",
        category="phishing",
        maintainer="Phishing Army",
        intensity="balanced",
        recommended=True,
    ),

    # ---------- Steven Black classique ----------
    FeedEntry(
        slug="stevenblack-unified",
        name="Steven Black Unified",
        description="Liste classique unifiée (pubs + malware + porn). Très large.",
        url="https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        category="all",
        maintainer="Steven Black",
        intensity="pro",
    ),

    # ---------- 1Hosts ----------
    FeedEntry(
        slug="1hosts-lite",
        name="1Hosts Lite",
        description="Liste légère 1Hosts, équivalent OISD Small.",
        url="https://o0.pages.dev/Lite/adblock.txt",
        category="ads",
        maintainer="1Hosts",
        intensity="light",
    ),
    FeedEntry(
        slug="1hosts-pro",
        name="1Hosts Pro",
        description="Liste 1Hosts agressive (pubs + traqueurs + analytics).",
        url="https://o0.pages.dev/Pro/adblock.txt",
        category="all",
        maintainer="1Hosts",
        intensity="pro",
    ),

    # ---------- Social / anti-microsoft / anti-google ----------
    FeedEntry(
        slug="hagezi-tracker-radio",
        name="HaGeZi Tracker Radio",
        description="Bloque télémétrie Smart-TV, IoT, électroménager connecté.",
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.iot.txt",
        category="tracking",
        maintainer="HaGeZi",
        intensity="balanced",
    ),

    # ---------- Anti-bypass DoH/VPN/Proxy (ferme le contournement client) ----------
    # NB: maintenue par HaGeZi, mise à jour quotidienne. Bloque les endpoints
    # DoH publics connus (cloudflare-dns.com, dns.google, mozilla.cloudflare-
    # dns.com, etc.) — quand un navigateur tente la résolution bootstrap pour
    # son DoH, le résolveur local renvoie NXDOMAIN et le navigateur bascule
    # automatiquement sur le DNS système (= AdGuard). Couvre aussi VPN/proxy
    # publics couramment utilisés pour bypass.
    FeedEntry(
        slug="hagezi-doh-vpn",
        name="HaGeZi DoH/VPN/Proxy bypass",
        description=(
            "Bloque les endpoints DoH publics (Firefox/Chrome Secure DNS, "
            "etc.) + VPN/proxies pour empêcher le contournement du résolveur "
            "local. Recommandé sur réseaux famille/invité."
        ),
        url="https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh-vpn-proxy-bypass.txt",
        category="malware",  # closest existing category — c'est un anti-bypass
        maintainer="HaGeZi",
        intensity="hard",
    ),
]


def get_feed(slug: str) -> FeedEntry | None:
    for f in CATALOG:
        if f.slug == slug:
            return f
    return None


def get_recommended() -> list[FeedEntry]:
    return [f for f in CATALOG if f.recommended]
