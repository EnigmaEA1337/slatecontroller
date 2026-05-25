"""Pydantic models for inventory + vulnerability findings + exploit enrichment."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "unknown"]
SourceId = Literal["openwrt-advisory", "osv", "nvd", "glinet-bulletin"]
AttackVector = Literal["network", "adjacent", "local", "physical", "unknown"]
ExploitMaturity = Literal[
    "none",         # no public exploit known
    "poc",          # proof of concept (often unreliable)
    "functional",   # verified functional exploit (Exploit-DB verified)
    "weaponized",   # ready-to-use tool (Metasploit module, armed Nuclei tpl)
    "in_the_wild",  # actively exploited in the wild (CISA KEV)
]
PriorityLevel = Literal["critical", "high", "medium", "low", "info"]


class Package(BaseModel):
    """One opkg-installed package."""

    name: str
    version: str
    # Upstream version with the OpenWrt revision suffix stripped.
    # E.g. "1.33.2-5" → upstream "1.33.2". Used for OSV/NVD matching.
    upstream_version: str
    # True for packages clearly added by GL.iNet (gl-* prefix, ated_ext, etc.)
    # — those have no reliable upstream → flagged as "non scanné" in UI.
    vendor_specific: bool = False


class Inventory(BaseModel):
    """Full SBOM snapshot of a device at a point in time."""

    taken_at: datetime
    openwrt_distrib_id: str
    openwrt_release: str  # "21.02-SNAPSHOT"
    openwrt_target: str
    openwrt_arch: str
    openwrt_taints: str
    firmware_version: str  # e.g. "4.8.4" from GL.iNet RPC system.get_info
    kernel: str  # "5.4.281"
    board_name: str
    hostname: str
    model: str
    packages: list[Package] = Field(default_factory=list)

    @property
    def package_count(self) -> int:
        return len(self.packages)


class AttackPath(BaseModel):
    """Chain from a CVE to the adversary techniques that exploit it.

    Populated by the CVE2CAPEC enricher (https://github.com/Galeax/CVE2CAPEC).
    Empty lists are legal — many CVEs only resolve as far as a CWE.
    """

    cwe: list[str] = Field(default_factory=list)        # weakness IDs, e.g. "79"
    capec: list[str] = Field(default_factory=list)      # attack pattern IDs
    techniques: list[str] = Field(default_factory=list) # MITRE ATT&CK "T1027"
    atlas: list[str] = Field(default_factory=list)      # MITRE ATLAS (ML/AI)


class KEVEntry(BaseModel):
    """One entry from CISA's Known Exploited Vulnerabilities catalog."""

    date_added: datetime
    due_date: datetime | None = None
    vendor: str | None = None
    product: str | None = None
    vulnerability_name: str | None = None
    short_description: str | None = None
    required_action: str | None = None
    known_ransomware_use: bool = False
    notes: str | None = None


class EPSSData(BaseModel):
    """Exploit Prediction Scoring System data point (FIRST.org)."""

    score: float = Field(ge=0.0, le=1.0)         # probability of exploit in next 30 days
    percentile: float = Field(ge=0.0, le=1.0)     # rank vs all scored CVEs
    date: datetime


class ExploitSource(BaseModel):
    """One pointer to a public exploit for a CVE."""

    source: str                          # "exploit-db" | "github" | "metasploit" | "nuclei"
    url: str
    title: str | None = None
    author: str | None = None
    date_published: datetime | None = None
    verified: bool = False               # vendor-verified (Exploit-DB) or rank for MSF
    stars: int | None = None             # GitHub popularity


class CertFrBulletinRef(BaseModel):
    """Lightweight reference to a CERT-FR/ANSSI bulletin tied to a CVE."""

    ref: str                       # "CERTFR-2026-ALE-005"
    kind: Literal["alerte", "avis"]
    title: str
    url: str
    pub_date: datetime | None = None
    actively_exploited: bool = False
    ransomware_mentioned: bool = False


class ExploitEnrichment(BaseModel):
    """Cross-source exploit context for a single CVE.

    Populated by `ExploitEnricher`. Per-CVE, refreshed daily. Joined to
    Findings at view time (not stored on the finding row) so KEV/EPSS
    updates show up without re-scanning the device.
    """

    cve_id: str
    kev: KEVEntry | None = None
    epss: EPSSData | None = None
    exploit_db: list[ExploitSource] = Field(default_factory=list)
    github_pocs: list[ExploitSource] = Field(default_factory=list)
    metasploit_modules: list[ExploitSource] = Field(default_factory=list)
    cert_fr: list[CertFrBulletinRef] = Field(default_factory=list)
    exploit_maturity: ExploitMaturity = "none"
    priority_score: float = Field(default=0.0, ge=0.0, le=100.0)
    priority_level: PriorityLevel = "info"
    last_refreshed_at: datetime | None = None
    # Per-source error notes when a lookup failed (rare, non-blocking).
    errors: list[str] = Field(default_factory=list)

    @property
    def in_kev(self) -> bool:
        return self.kev is not None

    @property
    def in_cert_fr_alerte(self) -> bool:
        return any(b.kind == "alerte" for b in self.cert_fr)


class Finding(BaseModel):
    """One CVE matched against an installed package."""

    cve_id: str  # "CVE-2024-1234" or "OSV-..." or "OPENWRT-SA-..."
    package_name: str
    package_version: str
    severity: Severity = "unknown"
    source: SourceId
    fixed_in: str | None = None
    url: str | None = None
    summary: str = ""
    # CVSS v3 base score if known (NVD enrichment). 0..10.
    cvss_score: float | None = None
    # Full CVSS v3 vector string ("CVSS:3.1/AV:N/AC:L/..."). Lets the UI show
    # attack vector (remote vs local), privileges required, user interaction
    # independently of the score.
    cvss_vector: str | None = None
    # Set by enrichment after primary match. CVE id may not start with "CVE-"
    # for some sources (e.g. GHSA), so we keep `cve_id` as primary identifier.
    aliases: list[str] = Field(default_factory=list)
    # Filled by Cve2CapecEnricher after the source scan. None when no entry.
    attack_path: AttackPath | None = None
    # Joined at view time from the cve_exploit_cache table — NOT persisted on
    # the finding row, so daily refreshes of KEV/EPSS surface immediately.
    exploit: ExploitEnrichment | None = None


def parse_attack_vector(cvss_vector: str | None) -> AttackVector:
    """Derive AttackVector from the AV: field of a CVSS v3 vector string.

    Examples:
        "CVSS:3.1/AV:N/AC:L/..."  → "network"
        "CVSS:3.0/AV:L/..."        → "local"
        None or unparseable        → "unknown"

    Why this matters for triage: AV:N (network) is exploitable remote and is
    massively higher-risk than AV:L (local) which needs the attacker to
    already have a shell.
    """
    if not cvss_vector:
        return "unknown"
    for part in cvss_vector.split("/"):
        if part.startswith("AV:"):
            code = part[3:]
            return {
                "N": "network",
                "A": "adjacent",
                "L": "local",
                "P": "physical",
            }.get(code, "unknown")
    return "unknown"
