"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VPNConfigRow(Base):
    """One stored WireGuard config (e.g. uploaded from Proton's portal).

    Public fields stored plaintext for fast listing. The private key is
    Fernet-encrypted (key derived from `JWT_SECRET`).
    """

    __tablename__ = "vpn_configs"
    __table_args__ = (UniqueConstraint("name", name="uq_vpn_configs_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="proton")

    interface_address: Mapped[str] = mapped_column(String(128))
    dns_servers: Mapped[str] = mapped_column(String(256), default="")

    peer_public_key: Mapped[str] = mapped_column(String(64))
    peer_endpoint: Mapped[str] = mapped_column(String(128))
    peer_allowed_ips: Mapped[str] = mapped_column(String(256), default="0.0.0.0/0")

    private_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class ProfileRow(Base):
    """A contextual profile stored in DB.

    `payload` carries the full Profile spec (vpn/tor/tailscale/…) as JSON so we
    don't fight an ORM mapping for every sub-block. The `source` column tells
    us whether this row was seeded from a shipped YAML template or created by
    the user via the UI.
    """

    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_profiles_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ProfileWallpaperRow(Base):
    """User-uploaded background image attached to a profile (per kind).

    Two kinds per profile:
      - 'home' → /etc/gl_screen/wallpaper_home.png (nav screen)
      - 'lock' → /etc/gl_screen/wallpaper_wake_display.png (lock screen)

    Stored as BLOB so 5 profiles × 2 kinds × ~5MB cap is still trivial for
    SQLite and we get transactional consistency with profile rename/delete.

    `fit_mode` controls how we resize the original image onto the screen's
    320×240 canvas in the applier:
      - 'contain'  → letterbox/pillarbox, no crop (default — preserves all
                     content, dark margins fill the surplus space)
      - 'cover'    → center-crop to fill (old behavior; loses content at
                     the edges but no margins)
      - 'stretch'  → distort to fit (typically ugly, here for completeness)
    """

    __tablename__ = "profile_wallpapers"
    __table_args__ = (UniqueConstraint("profile_name", "kind", name="uq_profile_wallpapers_pn_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("profiles.name", ondelete="CASCADE", name="fk_profile_wallpapers_profile_name"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="home")
    fit_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="contain")
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class AppStateRow(Base):
    """Singleton key/value store for tiny global app state (active profile, …)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), default="")


class AppSecretRow(Base):
    """Encrypted secrets (SSH private key, future API tokens, …).

    All blobs are Fernet-encrypted; the key derives from `JWT_SECRET`.
    `metadata_json` carries non-secret context (creation date, fingerprint…).
    """

    __tablename__ = "app_secrets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class NetworkRow(Base):
    """A network/bridge definition (subnet + DHCP + LAN isolation).

    Each Wi-Fi SSID references one network by `slug`. By default we ship 3
    networks (lan, guest, iot) mirroring the Slate's stock bridges; the user
    can add custom VLAN-tagged networks for finer segmentation.
    """

    __tablename__ = "networks"
    __table_args__ = (UniqueConstraint("slug", name="uq_networks_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    bridge_name: Mapped[str] = mapped_column(String(32), nullable=False)
    subnet_cidr: Mapped[str] = mapped_column(String(32), nullable=False)
    gateway_ip: Mapped[str] = mapped_column(String(40), default="")
    dhcp_enabled: Mapped[bool] = mapped_column(default=True)
    isolated_from_lan: Mapped[bool] = mapped_column(default=False)
    vlan_tag: Mapped[int | None] = mapped_column(nullable=True)
    is_builtin: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(String(256), default="")
    # IPv6: subnet empty means "auto" (SLAAC + Prefix Delegation from WAN).
    ipv6_enabled: Mapped[bool] = mapped_column(default=False)
    ipv6_subnet_cidr: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DeviceRow(Base):
    """A GL.iNet device managed by the controller (Slate / Mudi / …).

    Status:
      - `pending`: row exists, creds tested OK, but adoption tasks not run yet.
      - `adopted`: hardening tasks ran successfully (or partially).
      - `error`: last probe failed (offline / wrong creds / TLS mismatch).
    """

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("slug", name="uq_devices_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="slate-7-pro")
    host: Mapped[str] = mapped_column(String(120), nullable=False)
    # Ordered list of admin URLs to try (LAN, Tailscale, WireGuard tunnel,
    # IPv6, etc.). The first one reachable is used. Stored as JSON array of
    # strings. Empty = fall back to `host` (legacy).
    admin_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    rpc_port: Mapped[int] = mapped_column(Integer, default=443)
    rpc_scheme: Mapped[str] = mapped_column(String(8), default="https")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    # SHA256 of the server's leaf cert, hex-encoded ("ab:cd:…"). Empty until
    # the TLS pinning step of adoption runs.
    tls_fingerprint_sha256: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    is_default: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(String(256), default="")
    last_probe_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    adopted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DeviceSecretRow(Base):
    """Per-device encrypted credentials and per-device secrets.

    Each device has its own SSH keypair, RPC username/password, etc. Keyed by
    `(device_id, kind)` so we can store multiple secret kinds without
    polluting AppSecretRow's global namespace.

    `kind` examples: 'rpc_password', 'ssh_keypair'.
    """

    __tablename__ = "device_secrets"
    __table_args__ = (
        UniqueConstraint("device_id", "kind", name="uq_device_secrets_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    # For rpc_password: encrypts the password string.
    # For ssh_keypair: encrypts the OpenSSH PEM (private key).
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class WifiSsidRow(Base):
    """A Wi-Fi SSID definition. Belongs to one network and has a stored PSK."""

    __tablename__ = "wifi_ssids"
    __table_args__ = (UniqueConstraint("slug", name="uq_wifi_ssids_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    ssid_name: Mapped[str] = mapped_column(String(32), nullable=False)
    band: Mapped[str] = mapped_column(String(8), nullable=False)
    security: Mapped[str] = mapped_column(String(16), nullable=False)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    network_slug: Mapped[str] = mapped_column(String(64), nullable=False, default="lan")
    client_isolation: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(String(256), default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DeviceInventorySnapshotRow(Base):
    """A point-in-time SBOM of a device.

    The full package list is stored as JSON to keep schema simple — querying
    individual packages happens in-app after deserialization. Vulnerability
    findings live in their own table, linked by snapshot_id, so they can be
    re-computed independently when the CVE feeds change.
    """

    __tablename__ = "device_inventory_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    openwrt_distrib_id: Mapped[str] = mapped_column(String(32), default="")
    openwrt_release: Mapped[str] = mapped_column(String(64), default="")
    openwrt_target: Mapped[str] = mapped_column(String(64), default="")
    openwrt_arch: Mapped[str] = mapped_column(String(64), default="")
    openwrt_taints: Mapped[str] = mapped_column(String(128), default="")
    firmware_version: Mapped[str] = mapped_column(String(32), default="")
    kernel: Mapped[str] = mapped_column(String(64), default="")
    board_name: Mapped[str] = mapped_column(String(64), default="")
    hostname: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    packages_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    package_count: Mapped[int] = mapped_column(Integer, default=0)
    scan_status: Mapped[str] = mapped_column(String(16), default="pending")
    scan_error: Mapped[str] = mapped_column(String(512), default="")


class VulnerabilityFindingRow(Base):
    """One CVE matched to one package in one snapshot.

    Deduped per (snapshot_id, source, cve_id, package_name). The matcher
    re-creates rows on each scan, so manual ack/notes live on a separate
    `vulnerability_acknowledgement` row keyed by (cve_id, package_name) —
    that way ack survives re-scans.
    """

    __tablename__ = "vulnerability_findings"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "source", "cve_id", "package_name",
            name="uq_vuln_finding_dedup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("device_inventory_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    cve_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package_name: Mapped[str] = mapped_column(String(128), nullable=False)
    package_version: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), default="unknown")
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    fixed_in: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str] = mapped_column(String(2048), default="")
    cvss_score: Mapped[float | None] = mapped_column(nullable=True)
    # Full CVSS v3 vector string, e.g. "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H".
    # We keep the raw form so the frontend can show attack vector / privileges
    # required / user interaction independently of the numeric score.
    cvss_vector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    aliases_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Optional chain CWE → CAPEC → MITRE ATT&CK → MITRE ATLAS, sourced from
    # github.com/Galeax/CVE2CAPEC and cached locally for 24 h. Shape:
    # {"cwe": ["79"], "capec": ["63"], "techniques": ["T1027"], "atlas": []}
    # None when enrichment never ran or the CVE wasn't in the dataset.
    attack_path_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CveAttackPathCacheRow(Base):
    """Cache of CVE2CAPEC lookups so a re-scan doesn't re-download year files.

    Re-fetched when `fetched_at` is older than 24 h. Rows are deleted when
    the source's year file no longer mentions that CVE id (rare).
    """

    __tablename__ = "cve_attack_path_cache"

    cve_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attack_path_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CveExploitCacheRow(Base):
    """Per-CVE exploit enrichment (KEV + EPSS + Exploit-DB + GitHub + MSF).

    Joined to Findings at view time so the user sees fresh KEV/EPSS data
    without re-scanning the device. Refreshed daily by the security
    scheduler; lazy-fetched on the first /findings call that needs a CVE
    not yet in cache.
    """

    __tablename__ = "cve_exploit_cache"

    cve_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enrichment_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Promoted to columns for cheap sorting/filtering at query time.
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_in_kev: Mapped[bool] = mapped_column(default=False)
    exploit_maturity: Mapped[str] = mapped_column(String(16), default="none")
    last_refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class VulnerabilityAcknowledgementRow(Base):
    """User-set ack/mute on a (cve_id, package_name) tuple.

    "Acknowledged" = "I've seen this, hide from default view".
    A weaker signal than `RiskAcceptanceRow` — see that table for explicit
    risk acceptance with rationale.

    Survives re-scans: when the matcher re-emits the same finding, the UI
    cross-references this table to mark it as acknowledged.
    """

    __tablename__ = "vulnerability_acknowledgements"
    __table_args__ = (
        UniqueConstraint(
            "cve_id", "package_name", name="uq_vuln_ack_pair"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cve_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package_name: Mapped[str] = mapped_column(String(128), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    note: Mapped[str] = mapped_column(String(512), default="")


class RiskAcceptanceRow(Base):
    """Explicit, documented risk acceptance for a (cve_id, package_name) tuple.

    Stronger than acknowledgement: requires a written reason and lets the
    user time-box the decision. Used for findings the user has decided to
    *not* patch — e.g. mitigations are in place, the affected feature isn't
    used, or the fix would break something more important.

    Survives re-scans and is shown distinctly in the UI (badge + filter).
    A risk acceptance with `expires_at` in the past auto-reverts back to
    "open" in the default view.
    """

    __tablename__ = "vulnerability_risk_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "cve_id", "package_name", name="uq_vuln_risk_pair"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cve_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package_name: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    reason: Mapped[str] = mapped_column(String(1024), default="")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DnsSecurityLevelRow(Base):
    """Editable copy of a DNS security level preset.

    Seeded at boot from `app.dns.security_levels.FACTORY_LEVELS` (those are
    still the source of truth for "reset to factory"). Once seeded, the row
    is the authoritative config — the manager reads from here, not from the
    Python constant. PATCH endpoint lets the user retune default provider,
    blocked services, toggles, etc.

    List-valued columns (`allowed_provider_slugs`, `blocked_services`,
    `adguard_blocklist_slugs`) are stored as JSON arrays of strings.
    """

    __tablename__ = "dns_security_levels"

    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), default="")
    icon: Mapped[str] = mapped_column(String(32), default="Shield")
    color: Mapped[str] = mapped_column(String(16), default="#3b82f6")
    default_provider_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_provider_slugs: Mapped[list[str]] = mapped_column(JSON, default=list)
    adguard_filtering: Mapped[bool] = mapped_column(default=False)
    safe_browsing: Mapped[bool] = mapped_column(default=False)
    parental_control: Mapped[bool] = mapped_column(default=False)
    safe_search: Mapped[bool] = mapped_column(default=False)
    blocked_services: Mapped[list[str]] = mapped_column(JSON, default=list)
    adguard_blocklist_slugs: Mapped[list[str]] = mapped_column(JSON, default=list)
    require_dot: Mapped[bool] = mapped_column(default=False)
    require_dnssec: Mapped[bool] = mapped_column(default=False)
    eu_only: Mapped[bool] = mapped_column(default=False)
    intensity: Mapped[str] = mapped_column(String(16), default="balanced")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class NetworkDnsProtectionRow(Base):
    """Mapping `network_slug → DNS security level (+ provider override)`.

    One row per network that has a DNS protection configured. Absent rows =
    no protection applied = network uses whatever the global Slate DNS gives
    it (typically dnsmasq → WAN upstream).

    When applied, the manager creates/updates an AdGuard Home "persistent
    client" identified by the network's CIDR with the upstream + filtering
    config dictated by the security level. See [[dns/security_levels]].
    """

    __tablename__ = "network_dns_protection"

    network_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    level_slug: Mapped[str] = mapped_column(String(32), nullable=False)
    # Null = use the level's default_provider_slug. Set when the user picks
    # a non-default provider within the level's allowed list.
    provider_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Name of the AdGuard client we created — kept for cleanup on removal,
    # since AdGuard's REST API identifies clients by name, not by CIDR.
    adguard_client_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
