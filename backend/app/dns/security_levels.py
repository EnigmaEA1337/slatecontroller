"""Niveaux de sécurité DNS — presets applicables par-network.

Chaque niveau est un *template de client AdGuard Home* : il définit
l'upstream DNS (un slug du [[catalog]]), si AdGuard fait du filtering en plus,
les blocklists, safe search, parental control, etc.

Appliqué via AdGuard Clients API (REST /control/clients/add|update). Le
mapping network_slug → level_slug est stocké en DB (table
`network_dns_protection`) et appliqué par `manager.DnsProtectionManager`.

Philosophie :
- **Léger** : juste un upstream rapide, aucune intervention AdGuard.
- **Standard** : upstream avec malware-blocking (Quad9, Cloudflare 1.1.1.2).
- **Famille** : upstream family + AdGuard parental + safe search forcé.
- **Souverain EU** : que des providers `is_eu_based=True`, no-log strict.
- **Paranoid** : upstream zero-trust (dns0.eu Zero) + toutes les TIF lists
  AdGuard + DNSSEC strict + safebrowsing + parental + safe search.

`FACTORY_LEVELS` est la source de vérité pour le SEED initial de la DB
(`app.dns.store.DnsSecurityLevelStore.ensure_seeded`) ET pour le bouton
"Reset to factory" côté UI. Au runtime, le manager lit depuis le store DB —
pas d'ici — pour respecter les édits utilisateur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.dns.catalog import get_provider

LevelSlug = Literal["leger", "standard", "famille", "souverain_eu", "paranoid"]
LevelIntensity = Literal["light", "balanced", "strict", "paranoid"]


@dataclass(frozen=True)
class SecurityLevel:
    """Un preset de configuration DNS pour un network.

    Mappé sur les champs de l'API AdGuard `/control/clients/add` :
      - `upstreams` ← provider DoT/DoH URL
      - `filtering_enabled` ← `adguard_filtering`
      - `safebrowsing_enabled` ← `safe_browsing`
      - `parental_enabled` ← `parental_control`
      - `safe_search.enabled` ← `safe_search`
      - `blocked_services` ← `blocked_services`
      - `use_global_blocked_services: false` toujours (on override)
      - `use_global_settings: false` toujours

    `adguard_blocklist_slugs` référence le catalog AdGuard ([[feeds]]) — ces
    blocklists doivent exister côté AdGuard (auto-pushées si besoin). Vide =
    le provider DNS fait tout le filtering, AdGuard ne rajoute rien.
    """

    slug: LevelSlug
    name: str
    description: str
    icon: str  # lucide-react icon name (Shield, ShieldCheck, etc.)
    color: str  # hex couleur pour la card UI

    # Provider DNS upstream par défaut. L'utilisateur peut override par-network.
    default_provider_slug: str

    # Liste blanche de providers acceptables pour ce niveau (UX : on ne montre
    # que ceux-ci dans le dropdown override). Si vide = tous les providers
    # du catalog sont acceptés.
    allowed_provider_slugs: list[str] = field(default_factory=list)

    # AdGuard Client config
    adguard_filtering: bool = False
    safe_browsing: bool = False
    parental_control: bool = False
    safe_search: bool = False
    blocked_services: list[str] = field(default_factory=list)
    adguard_blocklist_slugs: list[str] = field(default_factory=list)

    # Constraints
    require_dot: bool = False  # forcer un provider qui supporte DoT
    require_dnssec: bool = False
    eu_only: bool = False  # restreindre allowed_provider_slugs aux is_eu_based

    intensity: LevelIntensity = "balanced"


FACTORY_LEVELS: list[SecurityLevel] = [
    SecurityLevel(
        slug="leger",
        name="Léger",
        description=(
            "DNS rapide sans filtre. Pour réseau de confiance ou IoT qui a "
            "besoin d'un accès maximal."
        ),
        icon="Zap",
        color="#10b981",  # emerald
        default_provider_slug="cloudflare-classic",
        allowed_provider_slugs=[
            "cloudflare-classic",
            "quad9-unfiltered",
            "dns4eu-unfiltered",
            "dns0-open",
            "mullvad-base",
        ],
        adguard_filtering=False,
        safe_browsing=False,
        parental_control=False,
        safe_search=False,
        require_dot=False,
        intensity="light",
    ),
    SecurityLevel(
        slug="standard",
        name="Standard",
        description=(
            "Blocage malware + phishing côté upstream. Recommandé pour la "
            "majorité des réseaux."
        ),
        icon="Shield",
        color="#3b82f6",  # blue
        default_provider_slug="quad9-standard",
        allowed_provider_slugs=[
            "quad9-standard",
            "cloudflare-malware",
            "dns4eu-protective",
            "cira-shield-protected",
            "mullvad-adblock",
            "adguard-dns-default",
        ],
        adguard_filtering=False,  # le provider fait le filtering
        safe_browsing=True,
        parental_control=False,
        safe_search=False,
        require_dot=True,
        intensity="balanced",
    ),
    SecurityLevel(
        slug="famille",
        name="Famille",
        description=(
            "Blocage adult + gambling + violence + safe search forcé. "
            "Pour réseau enfants / invités."
        ),
        icon="HeartHandshake",
        color="#ec4899",  # pink
        default_provider_slug="dns4eu-child",
        allowed_provider_slugs=[
            "dns4eu-child",
            "cloudflare-family",
            "mullvad-family",
            "mullvad-all",
            "adguard-dns-family",
            "cleanbrowsing-family",
            "dns0-kids",
        ],
        adguard_filtering=True,  # AdGuard ajoute parental + blocked_services
        safe_browsing=True,
        parental_control=True,
        safe_search=True,
        # NOTE: only AdGuard's built-in service IDs are valid here. The full
        # list lives at GET /control/blocked_services/all on the AdGuard REST
        # API (~118 entries). Picking unknown IDs (e.g. "pokerstars",
        # "snapchat_personal") triggers HTTP 400 on client add/update.
        blocked_services=[
            "tiktok", "tinder", "snapchat", "betfair", "betano", "onlyfans",
        ],
        require_dot=True,
        intensity="strict",
    ),
    SecurityLevel(
        slug="souverain_eu",
        name="Souverain EU",
        description=(
            "Aucune résolution hors UE. DNS4EU/dns0.eu/Mullvad uniquement. "
            "RGPD natif, no-log, juridiction européenne."
        ),
        icon="Flag",
        color="#1e40af",  # deep blue
        default_provider_slug="dns4eu-protective",
        allowed_provider_slugs=[
            "dns4eu-protective",
            "dns4eu-protective-ads",
            "dns4eu-unfiltered",
            "dns4eu-child",
            "dns0-zero",
            "dns0-kids",
            "dns0-open",
            "quad9-standard",  # Suisse, RGPD-équivalent
            "mullvad-base",
            "mullvad-adblock",
            "mullvad-family",
            "mullvad-all",
            "adguard-dns-default",
            "adguard-dns-family",
        ],
        adguard_filtering=False,
        safe_browsing=True,
        parental_control=False,
        safe_search=False,
        require_dot=True,
        eu_only=True,
        intensity="balanced",
    ),
    SecurityLevel(
        slug="paranoid",
        name="Paranoid",
        description=(
            "DNS zero-trust + DNSSEC strict + AdGuard avec toutes les TIF "
            "lists + safebrowsing + parental + safe search. Pour mission "
            "critique ou lockdown."
        ),
        icon="ShieldAlert",
        color="#dc2626",  # red
        default_provider_slug="dns0-zero",
        allowed_provider_slugs=[
            "dns0-zero",
            "quad9-standard",
            "dns4eu-protective",
            "dns4eu-protective-ads",
            "mullvad-all",
        ],
        adguard_filtering=True,
        safe_browsing=True,
        parental_control=True,
        safe_search=True,
        blocked_services=[
            "tiktok", "facebook", "instagram", "twitter", "snapchat",
            "tinder", "youtube", "reddit", "discord", "onlyfans",
        ],
        adguard_blocklist_slugs=[
            "hagezi-tif",       # threat intel feeds
            "hagezi-pro-plus",  # ads + tracking + mining + fingerprinting
            "phishing-army",
            "oisd-big",
        ],
        require_dot=True,
        require_dnssec=True,
        eu_only=False,
        intensity="paranoid",
    ),
]


def get_factory_level(slug: str) -> SecurityLevel | None:
    """Lookup in the immutable factory defaults — used by reset endpoint."""
    for level in FACTORY_LEVELS:
        if level.slug == slug:
            return level
    return None


def validate_provider_for_level(level: SecurityLevel, provider_slug: str) -> str | None:
    """Return None if the provider is valid for this level, else an error message.

    Checks:
    - Provider exists in the catalog
    - If `allowed_provider_slugs` is set, provider must be in it
    - If `require_dot`, provider must have a `dot_hostname`
    - If `eu_only`, provider must have `is_eu_based=True`
    - If `require_dnssec`, provider must have `supports_dnssec=True`
    """
    provider = get_provider(provider_slug)
    if provider is None:
        return f"provider '{provider_slug}' introuvable dans le catalog"
    if level.allowed_provider_slugs and provider_slug not in level.allowed_provider_slugs:
        return (
            f"provider '{provider_slug}' non autorisé pour le niveau "
            f"'{level.slug}' (allowed: {level.allowed_provider_slugs})"
        )
    if level.require_dot and not provider.dot_hostname:
        return f"provider '{provider_slug}' ne supporte pas DoT (requis par '{level.slug}')"
    if level.eu_only and not provider.is_eu_based:
        return (
            f"provider '{provider_slug}' n'est pas EU-based "
            f"(requis par niveau '{level.slug}')"
        )
    if level.require_dnssec and not provider.supports_dnssec:
        return (
            f"provider '{provider_slug}' ne supporte pas DNSSEC "
            f"(requis par '{level.slug}')"
        )
    return None
