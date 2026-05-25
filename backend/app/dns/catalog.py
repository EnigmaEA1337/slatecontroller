"""Catalogue de résolveurs DNS publics gratuits, curé pour la sécurité.

Liste statique — change rarement, pas besoin de DB. Référencée par les
security_levels et exposée à l'UI via `GET /api/dns/catalog`.

Critères d'inclusion :
- Gratuit, public, sans inscription
- Politique de log claire (no-log ou anonymisée court terme)
- Support DoT (port 853) ET/OU DoH minimum — pas de provider plain-UDP-only
- Maintenu activement (organisations sérieuses, pas de projets abandonnés)

Sources principalement EU (DNS4EU, dns0.eu, Mullvad, Quad9-Suisse) +
quelques classiques (Cloudflare, AdGuard DNS, CIRA Shield).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FilterProfile = Literal["none", "malware", "family", "adblock", "custom"]
LogPolicy = Literal["none", "anonymized", "24h", "logged"]
Intensity = Literal["light", "balanced", "strict"]


@dataclass(frozen=True)
class DnsProvider:
    """Un résolveur DNS public sélectionnable.

    `ipv4_primary` est la seule donnée strictement requise pour fallback en
    UDP/53. DoT et DoH sont l'usage principal (cf [[security_levels]]).
    """

    slug: str
    name: str
    organization: str
    country: str  # ISO country code or "EU" for pan-EU
    is_eu_based: bool

    # Plain DNS (fallback / pre-stubby)
    ipv4_primary: str
    ipv4_secondary: str = ""
    ipv6_primary: str = ""
    ipv6_secondary: str = ""

    # Encrypted transports
    doh_url: str = ""  # full URL incl. /dns-query
    dot_hostname: str = ""  # hostname presented in TLS cert
    dot_port: int = 853
    dot_auth_name: str = ""  # SPKI pin or PKIX hostname for stubby tls_auth_name

    # Policy
    filter_profile: FilterProfile = "none"
    log_policy: LogPolicy = "none"
    supports_dnssec: bool = True

    # Curation hints
    recommended: bool = False
    intensity: Intensity = "balanced"
    description: str = ""


CATALOG: list[DnsProvider] = [
    # ---------- Cloudflare (US, fastest globally, no-log) ----------
    DnsProvider(
        slug="cloudflare-classic",
        name="Cloudflare 1.1.1.1",
        organization="Cloudflare",
        country="US",
        is_eu_based=False,
        ipv4_primary="1.1.1.1",
        ipv4_secondary="1.0.0.1",
        ipv6_primary="2606:4700:4700::1111",
        ipv6_secondary="2606:4700:4700::1001",
        doh_url="https://cloudflare-dns.com/dns-query",
        dot_hostname="1.1.1.1",
        dot_auth_name="cloudflare-dns.com",
        filter_profile="none",
        log_policy="24h",
        intensity="light",
        recommended=True,
        description="Le plus rapide globalement. Aucun filtre. Logs anonymisés 24h.",
    ),
    DnsProvider(
        slug="cloudflare-malware",
        name="Cloudflare 1.1.1.2 (malware)",
        organization="Cloudflare",
        country="US",
        is_eu_based=False,
        ipv4_primary="1.1.1.2",
        ipv4_secondary="1.0.0.2",
        ipv6_primary="2606:4700:4700::1112",
        ipv6_secondary="2606:4700:4700::1002",
        doh_url="https://security.cloudflare-dns.com/dns-query",
        dot_hostname="1.1.1.2",
        dot_auth_name="security.cloudflare-dns.com",
        filter_profile="malware",
        log_policy="24h",
        intensity="balanced",
        recommended=True,
        description="Blocage malware + phishing. Identique 1.1.1.1 sinon.",
    ),
    DnsProvider(
        slug="cloudflare-family",
        name="Cloudflare 1.1.1.3 (family)",
        organization="Cloudflare",
        country="US",
        is_eu_based=False,
        ipv4_primary="1.1.1.3",
        ipv4_secondary="1.0.0.3",
        ipv6_primary="2606:4700:4700::1113",
        ipv6_secondary="2606:4700:4700::1003",
        doh_url="https://family.cloudflare-dns.com/dns-query",
        dot_hostname="1.1.1.3",
        dot_auth_name="family.cloudflare-dns.com",
        filter_profile="family",
        log_policy="24h",
        intensity="strict",
        description="Malware + phishing + adult content. Pour profil famille.",
    ),

    # ---------- Quad9 (Suisse, IBM/PCH, fondation non-lucrative) ----------
    DnsProvider(
        slug="quad9-standard",
        name="Quad9 9.9.9.9",
        organization="Quad9 Foundation",
        country="CH",
        is_eu_based=True,  # Suisse, RGPD-équivalent
        ipv4_primary="9.9.9.9",
        ipv4_secondary="149.112.112.112",
        ipv6_primary="2620:fe::fe",
        ipv6_secondary="2620:fe::9",
        doh_url="https://dns.quad9.net/dns-query",
        dot_hostname="9.9.9.9",
        dot_auth_name="dns.quad9.net",
        filter_profile="malware",
        log_policy="none",
        supports_dnssec=True,
        intensity="balanced",
        recommended=True,
        description="No-log strict. Malware blocking + DNSSEC enforced. Org neutre suisse.",
    ),
    DnsProvider(
        slug="quad9-unfiltered",
        name="Quad9 9.9.9.10 (unfiltered)",
        organization="Quad9 Foundation",
        country="CH",
        is_eu_based=True,
        ipv4_primary="9.9.9.10",
        ipv4_secondary="149.112.112.10",
        ipv6_primary="2620:fe::10",
        ipv6_secondary="2620:fe::fe:10",
        doh_url="https://dns10.quad9.net/dns-query",
        dot_hostname="9.9.9.10",
        dot_auth_name="dns10.quad9.net",
        filter_profile="none",
        log_policy="none",
        supports_dnssec=False,
        intensity="light",
        description="Quad9 sans filtrage ni DNSSEC. Pour debugging.",
    ),

    # ---------- DNS4EU (EU, projet Whalebone + Commission UE, 2024+) ----------
    DnsProvider(
        slug="dns4eu-protective",
        name="DNS4EU Protective",
        organization="Whalebone / Commission UE",
        country="EU",
        is_eu_based=True,
        ipv4_primary="86.54.11.1",
        ipv4_secondary="86.54.11.201",
        ipv6_primary="2a13:1001::86:54:11:1",
        doh_url="https://protective.joindns4.eu/dns-query",
        dot_hostname="protective.joindns4.eu",
        dot_auth_name="protective.joindns4.eu",
        filter_profile="malware",
        log_policy="none",
        intensity="balanced",
        recommended=True,
        description="DNS souverain EU. Protection malware/phishing/botnet. RGPD natif.",
    ),
    DnsProvider(
        slug="dns4eu-unfiltered",
        name="DNS4EU Unfiltered",
        organization="Whalebone / Commission UE",
        country="EU",
        is_eu_based=True,
        ipv4_primary="86.54.11.100",
        ipv4_secondary="86.54.11.200",
        ipv6_primary="2a13:1001::86:54:11:100",
        doh_url="https://unfiltered.joindns4.eu/dns-query",
        dot_hostname="unfiltered.joindns4.eu",
        dot_auth_name="unfiltered.joindns4.eu",
        filter_profile="none",
        log_policy="none",
        intensity="light",
        description="DNS4EU sans filtre. Souverain EU, no-log.",
    ),
    DnsProvider(
        slug="dns4eu-protective-ads",
        name="DNS4EU Protective + Ads",
        organization="Whalebone / Commission UE",
        country="EU",
        is_eu_based=True,
        ipv4_primary="86.54.11.12",
        ipv4_secondary="86.54.11.212",
        doh_url="https://ads-protective.joindns4.eu/dns-query",
        dot_hostname="ads-protective.joindns4.eu",
        dot_auth_name="ads-protective.joindns4.eu",
        filter_profile="adblock",
        log_policy="none",
        intensity="strict",
        description="DNS4EU + blocage pubs/trackers. Souverain EU.",
    ),
    DnsProvider(
        slug="dns4eu-child",
        name="DNS4EU Child Protection",
        organization="Whalebone / Commission UE",
        country="EU",
        is_eu_based=True,
        ipv4_primary="86.54.11.13",
        ipv4_secondary="86.54.11.213",
        doh_url="https://child.joindns4.eu/dns-query",
        dot_hostname="child.joindns4.eu",
        dot_auth_name="child.joindns4.eu",
        filter_profile="family",
        log_policy="none",
        intensity="strict",
        recommended=True,
        description="DNS4EU + filtres pour mineurs (adult/violence/gambling).",
    ),

    # ---------- dns0.eu (EU, fondation française, 2023+) ----------
    DnsProvider(
        slug="dns0-zero",
        name="dns0.eu Zero (paranoid)",
        organization="dns0.eu",
        country="FR",
        is_eu_based=True,
        ipv4_primary="193.110.81.9",
        ipv4_secondary="185.253.5.9",
        ipv6_primary="2a0f:fc80::9",
        ipv6_secondary="2a0f:fc81::9",
        doh_url="https://zero.dns0.eu",
        dot_hostname="zero.dns0.eu",
        dot_auth_name="zero.dns0.eu",
        filter_profile="malware",
        log_policy="none",
        intensity="strict",
        recommended=True,
        description="Bloque tout domaine non vérifié activement (zero-trust). FR-souverain.",
    ),
    DnsProvider(
        slug="dns0-kids",
        name="dns0.eu Kids",
        organization="dns0.eu",
        country="FR",
        is_eu_based=True,
        ipv4_primary="193.110.81.8",
        ipv4_secondary="185.253.5.8",
        doh_url="https://kids.dns0.eu",
        dot_hostname="kids.dns0.eu",
        dot_auth_name="kids.dns0.eu",
        filter_profile="family",
        log_policy="none",
        intensity="strict",
        description="dns0.eu + protection enfants. FR.",
    ),
    DnsProvider(
        slug="dns0-open",
        name="dns0.eu Open",
        organization="dns0.eu",
        country="FR",
        is_eu_based=True,
        ipv4_primary="193.110.81.1",
        ipv4_secondary="185.253.5.1",
        doh_url="https://open.dns0.eu",
        dot_hostname="open.dns0.eu",
        dot_auth_name="open.dns0.eu",
        filter_profile="none",
        log_policy="none",
        intensity="light",
        description="dns0.eu sans filtre. Pour mesurer le coût du filtrage.",
    ),

    # ---------- Mullvad DNS (Suède, anonyme, no-account) ----------
    DnsProvider(
        slug="mullvad-base",
        name="Mullvad DNS Base",
        organization="Mullvad VPN AB",
        country="SE",
        is_eu_based=True,
        ipv4_primary="194.242.2.2",
        ipv6_primary="2a07:e340::2",
        doh_url="https://dns.mullvad.net/dns-query",
        dot_hostname="dns.mullvad.net",
        dot_auth_name="dns.mullvad.net",
        filter_profile="none",
        log_policy="none",
        intensity="light",
        description="Mullvad sans filtre. SE, no-log strict, financé par leur VPN.",
    ),
    DnsProvider(
        slug="mullvad-adblock",
        name="Mullvad DNS Adblock+Tracker",
        organization="Mullvad VPN AB",
        country="SE",
        is_eu_based=True,
        ipv4_primary="194.242.2.4",
        ipv6_primary="2a07:e340::4",
        doh_url="https://adblock.dns.mullvad.net/dns-query",
        dot_hostname="adblock.dns.mullvad.net",
        dot_auth_name="adblock.dns.mullvad.net",
        filter_profile="adblock",
        log_policy="none",
        intensity="balanced",
        description="Mullvad + adblock + trackers (analytics, fingerprinting).",
    ),
    DnsProvider(
        slug="mullvad-family",
        name="Mullvad DNS Family",
        organization="Mullvad VPN AB",
        country="SE",
        is_eu_based=True,
        ipv4_primary="194.242.2.6",
        ipv6_primary="2a07:e340::6",
        doh_url="https://family.dns.mullvad.net/dns-query",
        dot_hostname="family.dns.mullvad.net",
        dot_auth_name="family.dns.mullvad.net",
        filter_profile="family",
        log_policy="none",
        intensity="strict",
        description="Mullvad + adult + gambling + adblock.",
    ),
    DnsProvider(
        slug="mullvad-all",
        name="Mullvad DNS All (maximal)",
        organization="Mullvad VPN AB",
        country="SE",
        is_eu_based=True,
        ipv4_primary="194.242.2.9",
        ipv6_primary="2a07:e340::9",
        doh_url="https://all.dns.mullvad.net/dns-query",
        dot_hostname="all.dns.mullvad.net",
        dot_auth_name="all.dns.mullvad.net",
        filter_profile="family",
        log_policy="none",
        intensity="strict",
        description="Mullvad complet: ads + trackers + malware + adult + social + gambling.",
    ),

    # ---------- AdGuard DNS (Chypre, public, distinct du AdGuardHome local) ----------
    DnsProvider(
        slug="adguard-dns-default",
        name="AdGuard DNS Default",
        organization="AdGuard",
        country="CY",
        is_eu_based=True,
        ipv4_primary="94.140.14.14",
        ipv4_secondary="94.140.15.15",
        ipv6_primary="2a10:50c0::ad1:ff",
        ipv6_secondary="2a10:50c0::ad2:ff",
        doh_url="https://dns.adguard-dns.com/dns-query",
        dot_hostname="dns.adguard-dns.com",
        dot_auth_name="dns.adguard-dns.com",
        filter_profile="adblock",
        log_policy="anonymized",
        intensity="balanced",
        description="Bloque ads/trackers/malware. Provider AdGuard public, pas le daemon local.",
    ),
    DnsProvider(
        slug="adguard-dns-family",
        name="AdGuard DNS Family",
        organization="AdGuard",
        country="CY",
        is_eu_based=True,
        ipv4_primary="94.140.14.15",
        ipv4_secondary="94.140.15.16",
        doh_url="https://family.adguard-dns.com/dns-query",
        dot_hostname="family.adguard-dns.com",
        dot_auth_name="family.adguard-dns.com",
        filter_profile="family",
        log_policy="anonymized",
        intensity="strict",
        description="AdGuard DNS + adult content blocking + safe search.",
    ),

    # ---------- CleanBrowsing (US, gratuit famille) ----------
    DnsProvider(
        slug="cleanbrowsing-family",
        name="CleanBrowsing Family",
        organization="CleanBrowsing",
        country="US",
        is_eu_based=False,
        ipv4_primary="185.228.168.168",
        ipv4_secondary="185.228.169.168",
        ipv6_primary="2a0d:2a00:1::",
        ipv6_secondary="2a0d:2a00:2::",
        doh_url="https://doh.cleanbrowsing.org/doh/family-filter/",
        dot_hostname="family-filter-dns.cleanbrowsing.org",
        dot_auth_name="family-filter-dns.cleanbrowsing.org",
        filter_profile="family",
        log_policy="anonymized",
        intensity="strict",
        description="Force SafeSearch sur Google/Bing/YouTube. Pour profil enfants strict.",
    ),

    # ---------- CIRA Canadian Shield (CA, gov non-profit) ----------
    DnsProvider(
        slug="cira-shield-protected",
        name="CIRA Canadian Shield Protected",
        organization="CIRA (gouv. canadien)",
        country="CA",
        is_eu_based=False,
        ipv4_primary="149.112.121.10",
        ipv4_secondary="149.112.122.10",
        ipv6_primary="2620:10A:80BB::10",
        ipv6_secondary="2620:10A:80BC::10",
        doh_url="https://protected.canadianshield.cira.ca/dns-query",
        dot_hostname="protected.canadianshield.cira.ca",
        dot_auth_name="protected.canadianshield.cira.ca",
        filter_profile="malware",
        log_policy="none",
        intensity="balanced",
        description="DNS gouv. canadien. Malware/phishing. No-log. Sub-CA.",
    ),
]


def get_provider(slug: str) -> DnsProvider | None:
    for p in CATALOG:
        if p.slug == slug:
            return p
    return None


def get_recommended() -> list[DnsProvider]:
    return [p for p in CATALOG if p.recommended]


def filter_providers(
    *,
    eu_only: bool | None = None,
    filter_profile: FilterProfile | None = None,
    supports_dot: bool | None = None,
    supports_doh: bool | None = None,
) -> list[DnsProvider]:
    """Filter the catalog by common UI facets."""
    result = list(CATALOG)
    if eu_only is True:
        result = [p for p in result if p.is_eu_based]
    if filter_profile is not None:
        result = [p for p in result if p.filter_profile == filter_profile]
    if supports_dot is True:
        result = [p for p in result if p.dot_hostname]
    if supports_doh is True:
        result = [p for p in result if p.doh_url]
    return result
