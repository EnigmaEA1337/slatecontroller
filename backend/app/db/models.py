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
    """A network/bridge definition (subnet + DHCP + zone isolation).

    Each Wi-Fi SSID references one network by `slug`. Fresh installs ship
    with an EMPTY catalog — the user creates networks as they need. There
    is no "builtin" concept anymore : every row is user-managed and
    deletable (no `is_builtin` guard).

    Isolation is modeled in independent dimensions (see migrations
    a8b3c2d10e44 + b2c4d68e90f1 for the design rationale) :

    - ``intra_bridge_isolation``  L2 : ports of the same bridge cloisonnés
                                   (rare ; bridge `port_isolation`)
    - ``reach_internet``          L3 : forwarding to wan zone (default ON)
    - ``reachable_networks``      L3 : list of OTHER network slugs this
                                   one can route to (besides wan)

    The old single ``admin_access`` flag was split into three because
    "the Slate" is actually several services with very different exposure
    profiles. A guest network typically wants DHCP+DNS but absolutely
    no LuCI and no SSH ; an admin LAN wants all three. Lumping them
    cost us a misconfig where guests had LuCI access for a week.

    - ``services_access``         input policy for essential services :
                                   DHCP, DNS local (dnsmasq), ICMP.
                                   Default ON ; turning OFF makes the
                                   network unusable for most clients.
    - ``admin_ui_access``         input policy for LuCI + GL.iNet web UI
                                   (TCP 80 / 443). Default OFF — only
                                   trusted networks should reach the
                                   admin UI.
    - ``ssh_access``              input policy for SSH / dropbear
                                   (TCP 22). Default OFF — explicit opt-in
                                   per network.

    These are declarative ; the actual UCI ``config zone`` / ``config
    forwarding`` / per-service ``config rule`` sections are produced by
    the firewall handler at apply time (out of scope of this row).
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
    vlan_tag: Mapped[int | None] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(String(256), default="")
    # IPv6: subnet empty means "auto" (SLAAC + Prefix Delegation from WAN).
    ipv6_enabled: Mapped[bool] = mapped_column(default=False)
    ipv6_subnet_cidr: Mapped[str] = mapped_column(String(64), default="")

    # ── isolation 3-level model ──────────────────────────────────────
    intra_bridge_isolation: Mapped[bool] = mapped_column(default=False)
    reach_internet: Mapped[bool] = mapped_column(default=True)
    # JSON array of network slugs this one is allowed to reach besides
    # wan. Empty = isolated from all other subnets.
    reachable_networks: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Admin / management plane, split per service. See class docstring.
    services_access: Mapped[bool] = mapped_column(default=True)
    admin_ui_access: Mapped[bool] = mapped_column(default=False)
    ssh_access: Mapped[bool] = mapped_column(default=False)
    # Tailscale subnet routing. When True, the Slate advertises this
    # network's CIDR(s) as a subnet route on the tailnet, so peers can
    # reach hosts inside this subnet via 100.x.y.z. The agent generates
    # `tailscale up --advertise-routes=...` from every network that has
    # this flag set (and only those — networks the user explicitly
    # cloisonne stay invisible to remote tailnet peers).
    expose_to_tailnet: Mapped[bool] = mapped_column(default=False)

    # ── Per-network Tor routing ──────────────────────────────────────
    # ``tor_route_mode``  off / transparent / socks_only
    #   - off          : Tor is not involved for this network. Default.
    #   - transparent  : all WAN-bound traffic from this bridge is NATed
    #                    to Tor's TransPort. Clients see normal internet
    #                    but every connection exits via a Tor circuit.
    #                    Latency is high (250-800 ms) and throughput
    #                    capped (~1-3 Mbps) — only sensible for OSINT
    #                    / research networks.
    #   - socks_only   : the Tor daemon's SOCKS5 port is reachable on the
    #                    gateway IP (e.g. <gw>:9050) but no transparent
    #                    redirect is installed. Clients opt in per app.
    # ``tor_dns_over_tor``    when transparent, also redirect this
    #                          network's DNS to Tor's DNSPort. Avoids DNS
    #                          leaks to the upstream resolver. Ignored
    #                          when mode != transparent.
    # ``tor_kill_switch``     when transparent, if the Tor daemon is down
    #                          DROP the network's WAN egress (fail-closed)
    #                          so a tor crash doesn't silently leak the
    #                          real IP. Default off (fail-open).
    tor_route_mode: Mapped[str] = mapped_column(String(16), default="off")
    tor_dns_over_tor: Mapped[bool] = mapped_column(default=False)
    tor_kill_switch: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class TorSettingsRow(Base):
    """Global Tor daemon settings (singleton — id always 1).

    Per-network routing toggles live on :class:`NetworkRow`. This row holds
    the cross-cutting bits :

    - ``daemon_enabled``   master switch. When False, tor.sh stops the
                           daemon regardless of per-network requests
                           (also drops kill-switched networks to "no
                           Tor → no WAN" = fail-closed, by design).
    - ``use_bridges``      enables the ``UseBridges`` torrc directive.
                           Per-bridge lines come from :class:`TorBridgeRow`.
    """

    __tablename__ = "tor_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    daemon_enabled: Mapped[bool] = mapped_column(default=False)
    use_bridges: Mapped[bool] = mapped_column(default=False)
    # ISO-3166-1 alpha-2 lowercase ("ch", "de", "se") — empty = no
    # constraint, Tor picks the exit freely. When set, the handler emits
    # ``ExitNodes {xx}`` + ``StrictNodes 1`` so circuits that can't
    # satisfy the constraint fail rather than silently exiting elsewhere.
    exit_country_code: Mapped[str] = mapped_column(String(2), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class TorBridgeRow(Base):
    """A single Tor bridge entry the user pasted in the UI.

    ``bridge_line`` is the raw value the user would otherwise put after
    ``Bridge`` in torrc — we don't parse it, just write it through. The
    handler appends one ``Bridge <line>`` per enabled row when bridges are
    in use globally.
    """

    __tablename__ = "tor_bridges"

    id: Mapped[int] = mapped_column(primary_key=True)
    # obfs4 / webtunnel / snowflake / vanilla — informational; the line
    # itself already encodes the transport.
    kind: Mapped[str] = mapped_column(String(16), default="obfs4")
    bridge_line: Mapped[str] = mapped_column(String(512), nullable=False)
    note: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
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
    """A Wi-Fi SSID definition. Belongs to one network and has a stored PSK.

    An SSID is a pure layer-2 access definition (broadcast name, bands,
    security, PSK, client isolation). It is NOT bound to a network here —
    that mapping (SSID → bridge/subnet) lives on the profile, because the
    same SSID can route to different networks depending on the active
    profile, exactly like a physical switch port.

    An SSID may broadcast on several bands simultaneously (same name +
    PSK on 2.4 GHz and 5 GHz, for example). Bands are stored as a JSON
    list of tokens ``"2"`` / ``"5"`` / ``"6"``. The agent handler creates
    one ``wifi-iface`` section per band, all sharing the same ssid + key.

    ``mlo`` (Wi-Fi 7 Multi-Link Operation) is a separate flag : when
    True the agent builds a single MLD-bundled iface instead of N
    independent VAPs — clients capable of Wi-Fi 7 then aggregate the
    links. MLO requires at least two bands in ``bands``.
    """

    __tablename__ = "wifi_ssids"
    __table_args__ = (UniqueConstraint("slug", name="uq_wifi_ssids_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    ssid_name: Mapped[str] = mapped_column(String(32), nullable=False)
    # JSON array of band tokens : "2" (2.4 GHz), "5", "6". Always at
    # least one element. Empty list is invalid (enforced by Pydantic).
    bands: Mapped[list[str]] = mapped_column(JSON, default=list)
    mlo: Mapped[bool] = mapped_column(default=False)
    security: Mapped[str] = mapped_column(String(16), nullable=False)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    # NB: no network_slug here. An SSID is a pure L2 access definition
    # (name / bands / security / PSK / isolation). Which L3 network it
    # binds to is a per-PROFILE decision (the `network_slug` on each
    # profile's ssids[] ref), same as a physical switch port — the
    # binding is contextual.
    client_isolation: Mapped[bool] = mapped_column(default=False)
    # Hidden SSID : the AP doesn't include the SSID in beacon frames.
    # NOT a real security control — clients still beacon the name in
    # probe requests and the BSSID is always visible. Mostly cosmetic /
    # casual-listing avoidance ; kept here because it's a standard UCI
    # option users expect to see in the UI.
    hidden: Mapped[bool] = mapped_column(default=False)
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


class RadioConfigRow(Base):
    """Per-band radio (layer-1) configuration for a device.

    One row per (device_slug, band) tuple. Stores the channel/htmode/
    txpower/country the operator selected. Defaults are applied at the
    Pydantic layer when a row is missing (see ``wifi/radio_config.py``).
    """

    __tablename__ = "radio_configs"
    __table_args__ = (
        UniqueConstraint("device_slug", "band", name="uq_radio_configs_device_band"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    band: Mapped[str] = mapped_column(String(2), nullable=False)
    # 0 means "auto / ACS"; otherwise the operator-forced channel number.
    channel: Mapped[int] = mapped_column(default=0, nullable=False)
    htmode: Mapped[str] = mapped_column(String(16), default="EHT160", nullable=False)
    txpower_percent: Mapped[int] = mapped_column(default=100, nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="FR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ThreatEventRow(Base):
    """Persisted RF threat detection — one row per (kind, bssid, dismissed?).

    The scanner emits ThreatEvent records that the route persists here so
    the AUDIT → Air Watch page can list historical detections, let the
    operator dismiss false positives, and surface counts in the AUDIT
    score.
    """

    __tablename__ = "threat_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    bssid: Mapped[str] = mapped_column(String(17), nullable=False)
    ssid: Mapped[str] = mapped_column(String(128), default="")
    channel: Mapped[int] = mapped_column(default=0)
    rssi_dbm: Mapped[int] = mapped_column(default=-100)
    message: Mapped[str] = mapped_column(String(512), default="")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    dismissed: Mapped[bool] = mapped_column(default=False)
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class ScanHistoryRow(Base):
    """One persisted scan run — per (device, band, started_at).

    Stores the high-level outcome (neighbour count, threats, recommended
    channel) plus optional geolocation : ``lat`` / ``lon`` / ``accuracy_m``
    when the operator (or the wardrive daemon) tagged the scan with a
    position. ``source`` records who provided that fix so the UI can
    show "📱 phone GPS" vs "🛰 slate GPS" vs "📌 manual pin" provenance.
    """

    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    band: Mapped[str] = mapped_column(String(2), nullable=False)
    iface: Mapped[str] = mapped_column(String(16), default="")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True,
    )
    duration_s: Mapped[float] = mapped_column(default=0.0)

    # Geolocation : NULL when the scan wasn't tagged with a position.
    # Lat / lon stored as float ; accuracy in metres ; source descriptor
    # is one of "browser" / "gps_slate" / "manual" / "wardrive".
    lat: Mapped[float | None] = mapped_column(nullable=True)
    lon: Mapped[float | None] = mapped_column(nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="")

    neighbors_count: Mapped[int] = mapped_column(default=0)
    threats_count: Mapped[int] = mapped_column(default=0)
    recommended_channel: Mapped[int | None] = mapped_column(nullable=True)
    current_channel: Mapped[int | None] = mapped_column(nullable=True)

    # Free-text note the operator can attach ("client mission, point A").
    note: Mapped[str] = mapped_column(String(256), default="")


class ScanNeighborRow(Base):
    """One BSSID seen during a scan run. Many per ``ScanHistoryRow``."""

    __tablename__ = "scan_neighbors"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scan_history.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    bssid: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    ssid: Mapped[str] = mapped_column(String(128), default="")
    hidden: Mapped[bool] = mapped_column(default=False)
    channel: Mapped[int] = mapped_column(default=0)
    band: Mapped[str] = mapped_column(String(2), default="")
    rssi_dbm: Mapped[int] = mapped_column(default=-100)
    security: Mapped[str] = mapped_column(String(32), default="")
    ht_mode: Mapped[str] = mapped_column(String(16), default="")
    is_wps_enabled: Mapped[bool] = mapped_column(default=False)


class BssidWigleCacheRow(Base):
    """Local cache of WiGLE.net lookups so we don't burn quota every scan.

    One row per BSSID we've ever asked WiGLE about. NULL ``lat``/``lon``
    means "WiGLE didn't have a fix for this BSSID" — we still keep the
    row to avoid re-querying within the TTL window.
    """

    __tablename__ = "bssid_wigle_cache"

    bssid: Mapped[str] = mapped_column(String(17), primary_key=True)
    lat: Mapped[float | None] = mapped_column(nullable=True)
    lon: Mapped[float | None] = mapped_column(nullable=True)
    qos: Mapped[int] = mapped_column(default=0)   # WiGLE quality 0-7
    first_seen_at: Mapped[str] = mapped_column(String(32), default="")
    last_seen_at: Mapped[str] = mapped_column(String(32), default="")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )
    not_found: Mapped[bool] = mapped_column(default=False)


class DeviceLocationRow(Base):
    """Per-device location entries, kept as a history timeline.

    A device (Slate) is mobile — it can be at the office in the morning,
    on a mission in the afternoon, back home in the evening. Each
    location entry captures one such point with a label and an optional
    note. The MOST RECENT entry is the device's "current" location and
    is what gets stamped onto fresh scans (unless the operator explicitly
    overrides at scan-time with the browser GPS or a different pin).

    ``source`` mirrors the scan source vocabulary :
        "manual"     operator typed in coords
        "browser"    captured from browser geolocation
        "gps_slate"  came from the Slate's USB GPS dongle (via gpsd)
        "wardrive"   recorded automatically during a wardrive run
    """

    __tablename__ = "device_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_slug: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    lat: Mapped[float] = mapped_column(nullable=False)
    lon: Mapped[float] = mapped_column(nullable=False)
    accuracy_m: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    label: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC), index=True,
    )
