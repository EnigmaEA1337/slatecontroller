"""Device-level security hardening gauge for the Slate.

Independent from per-profile scores: this measures the Slate itself —
firmware freshness, admin services posture, exposed protocols.

Each check uses one or more RPC calls discovered via the firmware's API
description (see `scripts/probe_api_methods.py`). When a needed method isn't
exposed by this firmware, the check is marked `needs_probe` (0 points, no
penalty) so the gauge stays honest about its blind spots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import structlog

from app.adguard.manager import AdGuardError, AdGuardManager
from app.exceptions import SlateRpcError, SlateUnreachableError
from app.security.exploit_enricher import ExploitEnricher
from app.security.store import SecurityStore
from app.slate.client import SlateClient
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

CheckStatus = Literal["ready", "needs_probe", "skipped", "error"]


@dataclass
class HardeningCheck:
    name: str
    points: int
    max_points: int
    status: CheckStatus
    note: str = ""


@dataclass
class HardeningReport:
    score: int
    max_score: int
    reachable: bool
    checks: list[HardeningCheck] = field(default_factory=list)

    @property
    def percent(self) -> int:
        if self.max_score == 0:
            return 0
        return round(self.score * 100 / self.max_score)


def _unwrap_result(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if hasattr(payload, "result") and not isinstance(payload, dict):
        inner = payload.result
        if isinstance(inner, dict):
            return inner
        return _unwrap_result(inner)
    if isinstance(payload, dict):
        if "result" in payload and isinstance(payload["result"], dict):
            return payload["result"]
        return payload
    try:
        return dict(payload)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


async def _try_call(
    slate: SlateClient, group: str, method: str, params: Any = None
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = await slate.call(group, method, params)
        return _unwrap_result(raw), None
    except SlateRpcError as exc:
        return None, str(exc)


# ---------------------------- individual checks ---------------------------- #


async def _check_firmware_version(slate: SlateClient) -> HardeningCheck:
    info, err = await _try_call(slate, "system", "get_info")
    if info is None:
        return HardeningCheck(
            name="Firmware identifiable",
            points=0,
            max_points=10,
            status="needs_probe",
            note=err or "",
        )
    version = info.get("firmware_version") or ""
    if not version:
        return HardeningCheck(
            name="Firmware identifiable",
            points=0,
            max_points=10,
            status="error",
            note="aucune version retournée",
        )
    is_recent = version.startswith("4.8") or version.startswith("4.9")
    return HardeningCheck(
        name="Firmware récent (≥ 4.8.x)",
        points=10 if is_recent else 0,
        max_points=10,
        status="ready",
        note=f"version installée: {version}"
        + ("" if is_recent else " — mise à jour recommandée"),
    )


async def _check_device_initialized(slate: SlateClient) -> HardeningCheck:
    """`ui.check_initialized` confirms the first-boot wizard was completed —
    which forces the user to set a non-default admin password."""
    data, err = await _try_call(slate, "ui", "check_initialized")
    if data is None:
        return HardeningCheck(
            name="Device initialisé (mdp admin défini)",
            points=0,
            max_points=15,
            status="needs_probe",
            note=err or "",
        )
    initialized = bool(data.get("initialized"))
    return HardeningCheck(
        name="Device initialisé (mdp admin défini)",
        points=15 if initialized else 0,
        max_points=15,
        status="ready",
        note="setup wizard complété, mdp admin défini"
        if initialized
        else "DEVICE NON INITIALISÉ — password par défaut !",
    )


async def _check_https_forced(slate: SlateClient) -> HardeningCheck:
    data, err = await _try_call(slate, "system", "get_security_policy")
    if data is None:
        return HardeningCheck(
            name="Web UI HTTPS forcé",
            points=0,
            max_points=10,
            status="needs_probe",
            note=err or "",
        )
    https = bool(data.get("redirect_https"))
    return HardeningCheck(
        name="Web UI HTTPS forcé",
        points=10 if https else 0,
        max_points=10,
        status="ready",
        note="redirect_https=ON, pas de fallback clear-text"
        if https
        else "redirect_https=OFF — UI accessible en HTTP",
    )


async def _check_admin_lan_only(slate: SlateClient) -> HardeningCheck:
    """`security_rule=1` means admin UI is restricted (LAN-only by default)."""
    data, err = await _try_call(slate, "system", "get_security_policy")
    if data is None:
        return HardeningCheck(
            name="Admin UI restreinte au LAN",
            points=0,
            max_points=15,
            status="needs_probe",
            note=err or "",
        )
    secure = data.get("security_rule") == 1
    return HardeningCheck(
        name="Admin UI restreinte au LAN",
        points=15 if secure else 0,
        max_points=15,
        status="ready",
        note="security_rule=1, WAN ne peut pas atteindre l'admin UI"
        if secure
        else "security_rule=0 — admin UI potentiellement exposée au WAN",
    )


async def _check_no_custom_wan_rules(slate: SlateClient) -> HardeningCheck:
    """No user firewall rule opening WAN → safer default-deny stance."""
    data, err = await _try_call(slate, "firewall", "get_rule_list")
    if data is None:
        return HardeningCheck(
            name="Pas de règle firewall ouvrant WAN",
            points=0,
            max_points=10,
            status="needs_probe",
            note=err or "",
        )
    rules = data.get("res") or []
    wan_open_rules = [
        r for r in rules
        if isinstance(r, dict)
        and r.get("src") == "wan"
        and r.get("target", "").upper() in ("ACCEPT", "DNAT")
    ]
    return HardeningCheck(
        name="Pas de règle firewall ouvrant WAN",
        points=10 if not wan_open_rules else 0,
        max_points=10,
        status="ready",
        note=(
            f"{len(rules)} règle(s) custom — aucune ouverture WAN"
            if not wan_open_rules
            else f"{len(wan_open_rules)} règle(s) WAN ACCEPT/DNAT — exposition extérieure"
        ),
    )


async def _check_tailscale_running(slate: SlateClient) -> HardeningCheck:
    status, err = await _try_call(slate, "system", "get_status")
    if status is None:
        return HardeningCheck(
            name="Tailscale (canal admin de secours)",
            points=0,
            max_points=10,
            status="needs_probe",
            note=err or "",
        )
    services = status.get("service") or []
    running = any(
        isinstance(s, dict) and s.get("name") == "tailscale" and s.get("status") == 1
        for s in services
    )
    return HardeningCheck(
        name="Tailscale (canal admin de secours)",
        points=10 if running else 0,
        max_points=10,
        status="ready",
        note="actif" if running else "inactif — recommandé pour ne pas se locker",
    )


async def _check_adguard_running(slate: SlateClient) -> HardeningCheck:
    status, err = await _try_call(slate, "system", "get_status")
    if status is None:
        return HardeningCheck(
            name="AdGuard service actif",
            points=0,
            max_points=5,
            status="needs_probe",
            note=err or "",
        )
    services = status.get("service") or []
    running = any(
        isinstance(s, dict) and s.get("name") == "adguard" and s.get("status") == 1
        for s in services
    )
    return HardeningCheck(
        name="AdGuard service actif",
        points=5 if running else 0,
        max_points=5,
        status="ready",
        note="filtre DNS contre malware+tracking"
        if running
        else "désactivé — activer pour bloquer trackers/domaines malicieux",
    )


async def _check_wan_online(slate: SlateClient) -> HardeningCheck:
    status, err = await _try_call(slate, "system", "get_status")
    if status is None:
        return HardeningCheck(
            name="WAN opérationnel",
            points=0,
            max_points=5,
            status="needs_probe",
            note=err or "",
        )
    networks = status.get("network") or []
    wan_ifaces = {"wan", "wan6", "wwan", "wwan6", "tethering", "tethering6"}
    online = any(
        isinstance(n, dict) and n.get("interface") in wan_ifaces and n.get("online")
        for n in networks
    )
    return HardeningCheck(
        name="WAN opérationnel",
        points=5 if online else 0,
        max_points=5,
        status="ready",
        note="connectivité OK pour patchs/MAJ sécurité"
        if online
        else "WAN offline — pas de MAJ security possible",
    )


async def _check_temperature_safe(slate: SlateClient) -> HardeningCheck:
    status, err = await _try_call(slate, "system", "get_status")
    if status is None:
        return HardeningCheck(
            name="Température CPU saine",
            points=0,
            max_points=5,
            status="needs_probe",
            note=err or "",
        )
    sys_block = status.get("system") or {}
    temp = (sys_block.get("cpu") or {}).get("temperature")
    if temp is None:
        return HardeningCheck(
            name="Température CPU saine",
            points=0,
            max_points=5,
            status="error",
            note="capteur indisponible",
        )
    safe = temp < 80
    return HardeningCheck(
        name="Température CPU saine",
        points=5 if safe else 0,
        max_points=5,
        status="ready",
        note=f"{temp}°C ({'OK' if safe else 'élevée: vérifier ventilation/charge'})",
    )


async def _check_ipv6_enabled(slate: SlateClient) -> HardeningCheck:
    data, err = await _try_call(slate, "ipv6", "get_ipv6")
    if data is None:
        return HardeningCheck(
            name="Stack IPv6 active",
            points=0,
            max_points=5,
            status="needs_probe",
            note=err or "",
        )
    enabled = bool(data.get("enable"))
    return HardeningCheck(
        name="Stack IPv6 active",
        points=5 if enabled else 0,
        max_points=5,
        status="ready",
        note="dual-stack opérationnel"
        if enabled
        else "IPv6 OFF — clients restent en IPv4 only",
    )


# ---------------------------- SSH-based checks ---------------------------- #


async def _ssh_check_key_only(ssh: SlateSSH) -> HardeningCheck:
    """OpenWrt's dropbear: PasswordAuth=off (or 0) means key-only SSH."""
    try:
        result = await ssh.run("uci get dropbear.@dropbear[0].PasswordAuth 2>&1")
    except SlateSSHError as exc:
        return HardeningCheck(
            name="SSH key-only auth",
            points=0,
            max_points=15,
            status="needs_probe",
            note=f"SSH error: {exc}",
        )
    output = (result.stdout + result.stderr).strip().lower()
    if "entry not found" in output:
        # Option unset → dropbear defaults to PasswordAuth=on.
        return HardeningCheck(
            name="SSH key-only auth",
            points=0,
            max_points=15,
            status="ready",
            note="PasswordAuth non défini (= ON par défaut)",
        )
    if result.exit_status != 0:
        return HardeningCheck(
            name="SSH key-only auth",
            points=0,
            max_points=15,
            status="error",
            note=output[:120],
        )
    pw_off = output in ("off", "0", "no", "false")
    return HardeningCheck(
        name="SSH key-only auth",
        points=15 if pw_off else 0,
        max_points=15,
        status="ready",
        note=(
            "PasswordAuth=off — clé SSH obligatoire"
            if pw_off
            else f"PasswordAuth={output} — auth password encore active"
        ),
    )


async def _ssh_check_upnp_off(ssh: SlateSSH) -> HardeningCheck:
    """UPnP off (or package not installed) = no auto port-forwarding."""
    try:
        result = await ssh.run("uci get upnpd.config.enabled 2>&1")
    except SlateSSHError as exc:
        return HardeningCheck(
            name="UPnP désactivé",
            points=0,
            max_points=5,
            status="needs_probe",
            note=f"SSH error: {exc}",
        )
    output = (result.stdout + result.stderr).strip().lower()
    if "entry not found" in output:
        return HardeningCheck(
            name="UPnP désactivé",
            points=5,
            max_points=5,
            status="ready",
            note="config upnpd absente (paquet non installé/configuré)",
        )
    if result.exit_status != 0:
        return HardeningCheck(
            name="UPnP désactivé",
            points=0,
            max_points=5,
            status="error",
            note=output[:120],
        )
    disabled = output in ("0", "off", "no", "false")
    return HardeningCheck(
        name="UPnP désactivé",
        points=5 if disabled else 0,
        max_points=5,
        status="ready",
        note=f"upnpd.config.enabled={output or '(empty)'}",
    )


async def _ssh_check_screen_lock(ssh: SlateSSH) -> HardeningCheck:
    """Score the touchscreen PIN lock posture.

    The Slate's 2.4" screen exposes admin controls (toggle WiFi, see clients,
    open VPN config) once unlocked. Without a PIN, anyone with physical
    access (laptop bag pulled out at the airport, kid grabbing it from the
    desk) gets that control instantly.

    Scoring (out of 10):
      no PIN at all           → 0 pts ("vol/manip physique triviale")
      PIN but ENABLE=0        → 2 pts (configured but turned off!)
      weak PIN (1234/0000/…)  → 3 pts
      OK PIN but auto_lock>5min  → 6 pts
      OK PIN + reasonable lock   → 8 pts
      strong PIN + lock ≤2min   → 10 pts
    """
    from app.slate.screen_lock import ScreenLockError, get_status

    try:
        st = await get_status(ssh)
    except ScreenLockError as exc:
        return HardeningCheck(
            name="PIN écran tactile",
            points=0,
            max_points=10,
            status="needs_probe",
            note=f"SSH error: {exc}",
        )

    if not st.has_pin:
        return HardeningCheck(
            name="PIN écran tactile — ABSENT",
            points=0,
            max_points=10,
            status="ready",
            note=(
                "Aucun PIN configuré. L'écran tactile expose les contrôles "
                "admin (WiFi, VPN, clients) sans aucune barrière à un "
                "attaquant physique. Définir un PIN ≥ 6 chiffres."
            ),
        )

    if not st.enabled:
        return HardeningCheck(
            name="PIN écran tactile — désactivé",
            points=2,
            max_points=10,
            status="ready",
            note=(
                f"PIN configuré ({st.pin_length} chiffres) mais "
                "ENABLE_PASSCODE=0 — le verrouillage est inactif. "
                "Activer dans gl_screen.generic ou via l'API."
            ),
        )

    if st.pin_strength == "weak":
        return HardeningCheck(
            name="PIN écran tactile — faible",
            points=3,
            max_points=10,
            status="ready",
            note=(
                "Le PIN configuré est dans la liste des PINs triviaux "
                "(0000/1234/9999/année connue/…). Brute-force humain en "
                "10 essais."
            ),
        )

    auto_lock = st.auto_lock_seconds
    if auto_lock == 0 or auto_lock > 300:
        return HardeningCheck(
            name="PIN écran tactile — auto-lock trop long",
            points=6,
            max_points=10,
            status="ready",
            note=(
                f"PIN OK ({st.pin_length} chiffres, {st.pin_strength}) "
                f"mais auto-lock {auto_lock}s (>5min) — fenêtre d'attaque "
                "trop large si l'opérateur s'éloigne. Recommandé ≤ 120s."
            ),
        )

    if st.pin_strength == "medium" or auto_lock > 120:
        return HardeningCheck(
            name="PIN écran tactile",
            points=8,
            max_points=10,
            status="ready",
            note=(
                f"{st.pin_length} chiffres, force={st.pin_strength}, "
                f"auto-lock={auto_lock}s. Pour score plein : ≥6 chiffres "
                "non-triviaux + auto-lock ≤ 120s."
            ),
        )

    return HardeningCheck(
        name="PIN écran tactile",
        points=10,
        max_points=10,
        status="ready",
        note=(
            f"PIN fort ({st.pin_length} chiffres), auto-lock {auto_lock}s, "
            "verrouillage actif."
        ),
    )


async def _ssh_check_memory(ssh: SlateSSH) -> HardeningCheck:
    """Read /proc/meminfo and grade based on MemAvailable.

    The Slate 7 Pro ships with 1 GB RAM and no swap. Observed in the wild:
    OOM kills + dropbear unresponsive when MemAvailable drops below ~50 MB
    (the live device froze and required a physical reboot at 6 MB available
    on 2026-05-23 after a tailscaled/AdGuard leak filled it up over ~7 days).

    Thresholds:
      ≥ 200 MB → healthy (full points)
      100–200 MB → tight (half points; warn)
      50–100 MB → risk of imminent OOM (quarter points)
      < 50 MB → critical (zero points)
    """
    try:
        result = await ssh.run("grep -E '^(MemTotal|MemAvailable):' /proc/meminfo")
    except SlateSSHError as exc:
        return HardeningCheck(
            name="Mémoire disponible (≥ 200 MB)",
            points=0,
            max_points=10,
            status="needs_probe",
            note=f"SSH error: {exc}",
        )
    total_kb = 0
    avail_kb = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0].startswith("MemTotal"):
            total_kb = int(parts[1])
        elif parts[0].startswith("MemAvailable"):
            avail_kb = int(parts[1])
    if total_kb == 0:
        return HardeningCheck(
            name="Mémoire disponible (≥ 200 MB)",
            points=0,
            max_points=10,
            status="error",
            note="lecture /proc/meminfo: champs manquants",
        )
    avail_mb = avail_kb // 1024
    pct = round(avail_kb * 100 / total_kb)
    if avail_mb >= 200:
        return HardeningCheck(
            name="Mémoire disponible (≥ 200 MB)",
            points=10,
            max_points=10,
            status="ready",
            note=f"{avail_mb} MB disponibles ({pct}%) — sain",
        )
    if avail_mb >= 100:
        return HardeningCheck(
            name="Mémoire disponible (tendue)",
            points=5,
            max_points=10,
            status="ready",
            note=(
                f"{avail_mb} MB disponibles ({pct}%) — tendu. "
                "Surveiller la croissance, restart AdGuard/tailscaled "
                "peut libérer 50–100 MB."
            ),
        )
    if avail_mb >= 50:
        return HardeningCheck(
            name="Mémoire disponible — risque OOM",
            points=2,
            max_points=10,
            status="ready",
            note=(
                f"{avail_mb} MB disponibles ({pct}%) — risque OOM imminent. "
                "Redémarrer un daemon lourd (AdGuard/tailscaled) ou rebooter."
            ),
        )
    return HardeningCheck(
        name="Mémoire disponible — CRITIQUE",
        points=0,
        max_points=10,
        status="ready",
        note=(
            f"{avail_mb} MB disponibles ({pct}%) — OOM imminent ou en cours. "
            "Rebooter d'urgence."
        ),
    )


def _controller_admin_password_check() -> HardeningCheck:
    """Verify the controller's own admin password is not default/weak.

    The controller's admin account is the single entry point for the whole
    stack — a weak password here defeats SSH key-only on the Slate, AdGuard
    auth, profile management, everything. Independent from the Slate's RPC
    auth (which is `SLATE_PASSWORD` / SSH key).

    Reads `settings.admin_password` (not transmitted, not logged). The note
    only exposes length + verdict — never the password itself.

    Scoring:
      placeholder ("change-me", "admin", "password", empty…) → 0 pts (CRITICAL)
      < 12 chars → 3 pts (faible)
      12–19 chars → 8 pts (acceptable)
      ≥ 20 chars → 15 pts (fort)
    """
    from app.config import get_settings

    pwd = (get_settings().admin_password or "").strip()
    placeholders = {
        "", "change-me", "changeme", "password", "admin",
        "1234", "12345", "123456", "root", "default",
    }
    if pwd.lower() in placeholders:
        return HardeningCheck(
            name="Mot de passe admin contrôleur",
            points=0,
            max_points=15,
            status="ready",
            note=(
                "VALEUR PAR DÉFAUT détectée (change-me / admin / vide / …) — "
                "changer immédiatement ADMIN_PASSWORD dans .env. "
                "C'est l'auth racine de tout le stack."
            ),
        )
    n = len(pwd)
    if n < 12:
        return HardeningCheck(
            name="Mot de passe admin contrôleur (faible)",
            points=3,
            max_points=15,
            status="ready",
            note=(
                f"{n} caractères — brute-forçable en heures. "
                "Recommandé ≥ 20 chars aléatoires (password manager)."
            ),
        )
    if n < 20:
        return HardeningCheck(
            name="Mot de passe admin contrôleur",
            points=8,
            max_points=15,
            status="ready",
            note=f"{n} caractères — acceptable. Idéal ≥ 20 chars aléatoires.",
        )
    return HardeningCheck(
        name="Mot de passe admin contrôleur",
        points=15,
        max_points=15,
        status="ready",
        note=f"{n} caractères — fort",
    )


async def _adguard_dns_protection_check(
    manager: AdGuardManager | None,
) -> list[HardeningCheck]:
    """Two checks bundled together because they share one AdGuard REST round-trip:

    1. **DNSSEC validation** (5 pts) — `dnssec_enabled=true` on AdGuard. Without
       it the controller blindly trusts whatever the upstream resolver returns,
       so a BGP hijack of Quad9 / cache-poisoned upstream slips through.
    2. **HaGeZi DoH/VPN bypass blocklist active** (5 pts) — the catalog ships
       a curated list of well-known DoH/DoT endpoints + commercial VPN
       resolvers. Without it, a malicious app on a client (or a power-user
       browser) can bypass AdGuard entirely by speaking DoH to 1.1.1.1.

    Marked `needs_probe` when AdGuard is unreachable so the gauge stays
    honest about its blind spot.
    """
    if manager is None:
        return [
            HardeningCheck(
                name="Validation DNSSEC (AdGuard)", points=0, max_points=5,
                status="needs_probe", note="AdGuard manager indisponible",
            ),
            HardeningCheck(
                name="Blocklist anti-bypass DoH/VPN", points=0, max_points=5,
                status="needs_probe", note="AdGuard manager indisponible",
            ),
        ]

    dnssec_check: HardeningCheck
    try:
        cfg = await manager.get_dns_config()
        dnssec_on = bool(cfg.get("dnssec_enabled", False))
        dnssec_check = HardeningCheck(
            name="Validation DNSSEC (AdGuard)",
            points=5 if dnssec_on else 0,
            max_points=5,
            status="ready",
            note=(
                "RRSIG vérifiées localement — coupe les cache-poisoning + "
                "BGP-hijacks d'upstream resolver"
                if dnssec_on
                else "désactivée — AdGuard fait confiance aveuglément à "
                "l'upstream (Quad9/DNS4EU/Cloudflare). Activer pour fermer "
                "le gap cache-poisoning."
            ),
        )
    except AdGuardError as exc:
        dnssec_check = HardeningCheck(
            name="Validation DNSSEC (AdGuard)", points=0, max_points=5,
            status="needs_probe", note=f"AdGuard REST KO: {exc}",
        )

    doh_check: HardeningCheck
    try:
        filters = await manager.list_filters()
        # Marker convention: feeds.py tags filter names with [slate-ctrl].
        # The HaGeZi DoH/VPN list slug is "hagezi-doh-vpn".
        has_doh_blocklist = any(
            f.enabled and ("hagezi-doh-vpn" in f.url.lower()
                           or "doh" in f.name.lower())
            for f in filters
        )
        doh_check = HardeningCheck(
            name="Blocklist anti-bypass DoH/VPN",
            points=5 if has_doh_blocklist else 0,
            max_points=5,
            status="ready",
            note=(
                "active — bloque DoH/DoT publics + VPN-resolvers connus "
                "(coupe les apps qui contournent AdGuard via 1.1.1.1)"
                if has_doh_blocklist
                else "inactive — activer 'HaGeZi DoH/VPN/TLD' depuis "
                "Protection → DNS pour empêcher le bypass d'AdGuard"
            ),
        )
    except AdGuardError as exc:
        doh_check = HardeningCheck(
            name="Blocklist anti-bypass DoH/VPN", points=0, max_points=5,
            status="needs_probe", note=f"AdGuard REST KO: {exc}",
        )

    return [dnssec_check, doh_check]


@dataclass
class _Snapshot:
    """Pre-fetched payloads — built once per `compute_hardening` call so each
    Slate endpoint is hit at most once, no matter how many checks consume it.
    """

    info: dict[str, Any] | None = None
    info_err: str | None = None
    status: dict[str, Any] | None = None
    status_err: str | None = None
    security_policy: dict[str, Any] | None = None
    security_policy_err: str | None = None
    initialized: dict[str, Any] | None = None
    initialized_err: str | None = None
    firewall_rules: dict[str, Any] | None = None
    firewall_rules_err: str | None = None
    ipv6: dict[str, Any] | None = None
    ipv6_err: str | None = None


async def _build_snapshot(slate: SlateClient) -> _Snapshot:
    snap = _Snapshot()
    snap.info, snap.info_err = await _try_call(slate, "system", "get_info")
    snap.status, snap.status_err = await _try_call(slate, "system", "get_status")
    snap.security_policy, snap.security_policy_err = await _try_call(
        slate, "system", "get_security_policy"
    )
    snap.initialized, snap.initialized_err = await _try_call(
        slate, "ui", "check_initialized"
    )
    snap.firewall_rules, snap.firewall_rules_err = await _try_call(
        slate, "firewall", "get_rule_list"
    )
    snap.ipv6, snap.ipv6_err = await _try_call(slate, "ipv6", "get_ipv6")
    return snap


def _fw_check(snap: _Snapshot) -> HardeningCheck:
    if snap.info is None:
        return HardeningCheck("Firmware identifiable", 0, 10, "needs_probe", snap.info_err or "")
    v = snap.info.get("firmware_version") or ""
    if not v:
        return HardeningCheck("Firmware identifiable", 0, 10, "error", "aucune version retournée")
    is_recent = v.startswith("4.8") or v.startswith("4.9")
    return HardeningCheck(
        name="Firmware récent (≥ 4.8.x)",
        points=10 if is_recent else 0,
        max_points=10,
        status="ready",
        note=f"version installée: {v}" + ("" if is_recent else " — mise à jour recommandée"),
    )


def _init_check(snap: _Snapshot) -> HardeningCheck:
    if snap.initialized is None:
        return HardeningCheck("Device initialisé (mdp admin défini)", 0, 15, "needs_probe", snap.initialized_err or "")
    initd = bool(snap.initialized.get("initialized"))
    return HardeningCheck(
        name="Device initialisé (mdp admin défini)",
        points=15 if initd else 0,
        max_points=15,
        status="ready",
        note="setup wizard complété, mdp admin défini"
        if initd
        else "DEVICE NON INITIALISÉ — password par défaut !",
    )


def _https_check(snap: _Snapshot) -> HardeningCheck:
    if snap.security_policy is None:
        return HardeningCheck("Web UI HTTPS forcé", 0, 10, "needs_probe", snap.security_policy_err or "")
    on = bool(snap.security_policy.get("redirect_https"))
    return HardeningCheck(
        name="Web UI HTTPS forcé",
        points=10 if on else 0,
        max_points=10,
        status="ready",
        note="redirect_https=ON, pas de fallback clear-text"
        if on
        else "redirect_https=OFF — UI accessible en HTTP",
    )


def _admin_lan_check(snap: _Snapshot) -> HardeningCheck:
    if snap.security_policy is None:
        return HardeningCheck("Admin UI restreinte au LAN", 0, 15, "needs_probe", snap.security_policy_err or "")
    secure = snap.security_policy.get("security_rule") == 1
    return HardeningCheck(
        name="Admin UI restreinte au LAN",
        points=15 if secure else 0,
        max_points=15,
        status="ready",
        note="security_rule=1, WAN ne peut pas atteindre l'admin UI"
        if secure
        else "security_rule=0 — admin UI potentiellement exposée au WAN",
    )


def _wan_rules_check(snap: _Snapshot) -> HardeningCheck:
    if snap.firewall_rules is None:
        return HardeningCheck("Pas de règle firewall ouvrant WAN", 0, 10, "needs_probe", snap.firewall_rules_err or "")
    rules = snap.firewall_rules.get("res") or []
    wan_open = [
        r for r in rules
        if isinstance(r, dict)
        and r.get("src") == "wan"
        and r.get("target", "").upper() in ("ACCEPT", "DNAT")
    ]
    return HardeningCheck(
        name="Pas de règle firewall ouvrant WAN",
        points=10 if not wan_open else 0,
        max_points=10,
        status="ready",
        note=(
            f"{len(rules)} règle(s) custom — aucune ouverture WAN"
            if not wan_open
            else f"{len(wan_open)} règle(s) WAN ACCEPT/DNAT — exposition extérieure"
        ),
    )


def _service_check(snap: _Snapshot, svc: str, label: str, max_pts: int, on_note: str, off_note: str) -> HardeningCheck:
    if snap.status is None:
        return HardeningCheck(label, 0, max_pts, "needs_probe", snap.status_err or "")
    services = snap.status.get("service") or []
    running = any(
        isinstance(s, dict) and s.get("name") == svc and s.get("status") == 1
        for s in services
    )
    return HardeningCheck(
        name=label,
        points=max_pts if running else 0,
        max_points=max_pts,
        status="ready",
        note=on_note if running else off_note,
    )


def _wan_online_check(snap: _Snapshot) -> HardeningCheck:
    if snap.status is None:
        return HardeningCheck("WAN opérationnel", 0, 5, "needs_probe", snap.status_err or "")
    networks = snap.status.get("network") or []
    wan_ifaces = {"wan", "wan6", "wwan", "wwan6", "tethering", "tethering6"}
    online = any(
        isinstance(n, dict) and n.get("interface") in wan_ifaces and n.get("online")
        for n in networks
    )
    return HardeningCheck(
        name="WAN opérationnel",
        points=5 if online else 0,
        max_points=5,
        status="ready",
        note="connectivité OK pour patchs/MAJ sécurité"
        if online
        else "WAN offline — pas de MAJ security possible",
    )


def _temp_check(snap: _Snapshot) -> HardeningCheck:
    if snap.status is None:
        return HardeningCheck("Température CPU saine", 0, 5, "needs_probe", snap.status_err or "")
    sys_block = snap.status.get("system") or {}
    temp = (sys_block.get("cpu") or {}).get("temperature")
    if temp is None:
        return HardeningCheck("Température CPU saine", 0, 5, "error", "capteur indisponible")
    safe = temp < 80
    return HardeningCheck(
        name="Température CPU saine",
        points=5 if safe else 0,
        max_points=5,
        status="ready",
        note=f"{temp}°C ({'OK' if safe else 'élevée: vérifier ventilation/charge'})",
    )


def _ipv6_check(snap: _Snapshot) -> HardeningCheck:
    if snap.ipv6 is None:
        return HardeningCheck("Stack IPv6 active", 0, 5, "needs_probe", snap.ipv6_err or "")
    enabled = bool(snap.ipv6.get("enable"))
    return HardeningCheck(
        name="Stack IPv6 active",
        points=5 if enabled else 0,
        max_points=5,
        status="ready",
        note="dual-stack opérationnel"
        if enabled
        else "IPv6 OFF — clients restent en IPv4 only",
    )


async def _wifi_isolation_check(ssh: SlateSSH | None) -> HardeningCheck:
    """For every *active* SSID, check `option isolate=1` (AP isolation).

    AP isolation prevents two clients on the same SSID from talking to each
    other — a strong default for guest / OSINT / kids networks. It also
    breaks AirPlay / Chromecast / printers, so we don't pretend "100% =
    correct"; we just expose the ratio and let the user decide which SSID
    legitimately needs neighbors.

    Scoring (5 pts max):
      - Ratio of active SSID with isolate=1 → linear 0..5 pts
      - 0 active SSID → skipped (nothing to score)

    The note enumerates the *non-isolated* active SSID so the user knows
    where to act.
    """
    name = "Isolation client sur les SSID actifs"
    if ssh is None:
        return HardeningCheck(
            name=name, points=0, max_points=5,
            status="needs_probe", note="canal SSH désactivé",
        )

    try:
        result = await ssh.run("uci show wireless 2>/dev/null")
    except SlateSSHError as exc:
        return HardeningCheck(
            name=name, points=0, max_points=5,
            status="error", note=f"SSH a échoué: {exc}",
        )

    # Group keys by section (wireless.<section>.<attr>=<value>).
    sections: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("wireless."):
            continue
        try:
            key, value = line.split("=", 1)
        except ValueError:
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            continue
        _, section, attr = parts
        sections.setdefault(section, {})[attr] = value.strip().strip("'\"")

    # Active SSID = has .ssid and disabled != '1'.
    active_iso: list[tuple[str, str, bool]] = []  # (section, ssid, isolated)
    for section, attrs in sections.items():
        ssid = attrs.get("ssid")
        if not ssid:
            continue
        if attrs.get("disabled") == "1":
            continue
        isolated = attrs.get("isolate") == "1"
        active_iso.append((section, ssid, isolated))

    if not active_iso:
        return HardeningCheck(
            name=name, points=0, max_points=5,
            status="skipped", note="aucun SSID actif",
        )

    isolated_count = sum(1 for _, _, iso in active_iso if iso)
    total = len(active_iso)
    points = round(isolated_count / total * 5)
    non_iso = [ssid for _, ssid, iso in active_iso if not iso]

    if non_iso:
        sample = ", ".join(non_iso[:4])
        if len(non_iso) > 4:
            sample += f" +{len(non_iso) - 4}"
        note = f"{isolated_count}/{total} SSID isolés — non isolés: {sample}"
    else:
        note = f"{total}/{total} SSID actifs ont isolate=1"
    return HardeningCheck(
        name=name, points=points, max_points=5, status="ready", note=note,
    )


async def _admin_ip_whitelist_check(ssh: SlateSSH | None) -> HardeningCheck:
    """Detect firewall rules that restrict admin ports (22/80/443) to specific
    source IPs — a defense-in-depth layer on top of `Admin UI restreinte au LAN`.

    The default Slate config trusts the entire LAN zone for admin access. A
    rule with `src_ip` set + `dest_port` matching admin ports means the user
    explicitly whitelisted a subset of LAN clients.

    Scoring (5 pts max):
      - At least one such rule, enabled → 5 pts
      - No such rule → needs_probe (option available, no penalty)
      - SSH unavailable → needs_probe
    """
    name = "Whitelist IP pour l'admin UI"
    if ssh is None:
        return HardeningCheck(
            name=name, points=0, max_points=5,
            status="needs_probe", note="canal SSH désactivé",
        )

    try:
        result = await ssh.run("uci show firewall 2>/dev/null")
    except SlateSSHError as exc:
        return HardeningCheck(
            name=name, points=0, max_points=5,
            status="error", note=f"SSH a échoué: {exc}",
        )

    # Parse uci output → {section: {attr: value}}.
    sections: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("firewall."):
            continue
        try:
            key, value = line.split("=", 1)
        except ValueError:
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            continue
        _, section, attr = parts
        sections.setdefault(section, {})[attr] = value.strip().strip("'\"")

    admin_ports = {"22", "80", "443"}
    matches: list[str] = []
    for section, attrs in sections.items():
        # Only rule-type sections matter.
        if attrs.get(".type") and attrs.get(".type") != "rule":
            continue
        if attrs.get("enabled") == "0":
            continue
        if not attrs.get("src_ip"):
            continue
        # dest_port may be a space-separated list ("22 80 443") or a single port.
        dest_port = attrs.get("dest_port", "")
        ports = set(dest_port.split()) if dest_port else set()
        if ports & admin_ports:
            matches.append(f"{section}({attrs.get('src_ip')}→{dest_port})")

    if matches:
        sample = ", ".join(matches[:3])
        if len(matches) > 3:
            sample += f" +{len(matches) - 3}"
        return HardeningCheck(
            name=name, points=5, max_points=5,
            status="ready",
            note=f"{len(matches)} règle(s) whitelist détectée(s) — {sample}",
        )
    return HardeningCheck(
        name=name, points=0, max_points=5,
        status="needs_probe",
        note=(
            "aucune règle firewall ne restreint les ports 22/80/443 par IP source — "
            "défense en profondeur disponible mais non configurée"
        ),
    )


async def _luci_https_check(ssh: SlateSSH | None) -> HardeningCheck:
    """Probe LuCI (the OpenWrt admin UI) over HTTPS and HTTP.

    LuCI runs under the same uhttpd as the GL.iNet UI by default, but the
    user often enables HTTPS only for the GL.iNet root path. This check
    verifies LuCI specifically:
      - HTTPS to /cgi-bin/luci/ returns 200/302/303/403 (any "alive" code)
      - HTTP to /cgi-bin/luci/ redirects to HTTPS (302/303 with https:// Location)

    Scoring:
      - Both OK → 10/10
      - HTTPS OK but HTTP still serves LuCI in clear → 5/10
      - HTTPS unreachable → 0/10
      - LuCI not installed (404 both sides) → needs_probe (0 pts, no penalty)
    """
    name = "LuCI accessible en HTTPS uniquement"
    if ssh is None:
        return HardeningCheck(
            name=name, points=0, max_points=10,
            status="needs_probe", note="canal SSH désactivé",
        )

    host = ssh.host
    https_url = f"https://{host}/cgi-bin/luci/"
    http_url = f"http://{host}/cgi-bin/luci/"

    # verify=False est volontaire ici : LuCI tourne avec un certificat
    # auto-signé GL.iNet sur le LAN du Slate. Le contrôleur fait déjà du
    # certificate pinning sur l'endpoint RPC principal (cf
    # devices.tls.fetch_cert + tls_fingerprint_sha256 stocké par device).
    # Ce check est uniquement un test de présence du redirect HTTP→HTTPS,
    # pas un canal de confiance — on ne lit aucune donnée sensible ici.
    async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
        try:
            https_resp = await client.get(https_url, follow_redirects=False)
        except httpx.HTTPError as exc:
            return HardeningCheck(
                name=name, points=0, max_points=10,
                status="error", note=f"HTTPS injoignable : {exc}",
            )
        try:
            http_resp = await client.get(http_url, follow_redirects=False)
        except httpx.HTTPError as exc:
            return HardeningCheck(
                name=name, points=5, max_points=10,
                status="ready", note=f"HTTPS OK ({https_resp.status_code}), HTTP injoignable : {exc}",
            )

    # 404 on both means LuCI isn't installed at this path
    if https_resp.status_code == 404 and http_resp.status_code == 404:
        return HardeningCheck(
            name=name, points=0, max_points=10,
            status="needs_probe", note="paquet luci absent (HTTPS et HTTP renvoient 404)",
        )

    # HTTPS alive codes: 200, 302/303 (redirect to login), 403 (CSRF / not logged in)
    https_alive = https_resp.status_code in (200, 302, 303, 401, 403)
    if not https_alive:
        return HardeningCheck(
            name=name, points=0, max_points=10,
            status="ready", note=f"LuCI ne répond pas en HTTPS (code {https_resp.status_code})",
        )

    # HTTP should either 404 (disabled), or 301/302/303/308 to https://...
    location = http_resp.headers.get("location", "")
    http_redirects_to_https = (
        http_resp.status_code in (301, 302, 303, 308)
        and location.startswith("https://")
    )
    http_blocked = http_resp.status_code in (403, 404)

    if http_redirects_to_https or http_blocked:
        return HardeningCheck(
            name=name, points=10, max_points=10,
            status="ready",
            note=(
                f"HTTPS OK ({https_resp.status_code}) · HTTP "
                + ("redirige vers HTTPS" if http_redirects_to_https else f"bloqué ({http_resp.status_code})")
            ),
        )

    return HardeningCheck(
        name=name, points=5, max_points=10,
        status="ready",
        note=(
            f"HTTPS OK ({https_resp.status_code}), mais HTTP sert encore LuCI "
            f"({http_resp.status_code}) — activer la redirection HTTP→HTTPS"
        ),
    )


_EXPLOIT_MAX_POINTS = 25
_EXPLOIT_PENALTY_CRITICAL = 5   # priority_score >= 80
_EXPLOIT_PENALTY_HIGH = 2       # priority_score >= 60 and < 80
_EXPLOIT_PENALTY_MEDIUM = 1     # priority_score >= 40 and < 60


async def _exploit_check(
    device_id: int | None,
    security_store: SecurityStore | None,
    exploit_enricher: ExploitEnricher | None,
) -> HardeningCheck:
    """Score the device by the priority of its exploitable CVEs.

    Priority comes from `ExploitEnricher` (CVSS + EPSS + KEV + maturity).
    A CVSS-9.8 CVE *with* a Metasploit module *in* KEV pulls way harder than
    a dormant CVSS-9.8 — that's exactly what this check captures.

    Falls back gracefully:
      - no enricher / store / device → `skipped` (0 pts, no penalty)
      - no snapshot yet → `needs_probe`
    """
    name = "Exploits actifs (CVSS+EPSS+KEV+maturity)"
    if security_store is None or device_id is None or exploit_enricher is None:
        return HardeningCheck(
            name=name,
            points=0,
            max_points=_EXPLOIT_MAX_POINTS,
            status="skipped",
            note="enrichissement exploit non câblé",
        )

    snap = await security_store.latest_snapshot(device_id)
    if snap is None:
        return HardeningCheck(
            name=name,
            points=0,
            max_points=_EXPLOIT_MAX_POINTS,
            status="needs_probe",
            note="aucun scan effectué — déclenche-en un depuis /security",
        )

    findings = await security_store.list_findings(snap.id)
    acked = await security_store.acked_keys()
    risk_accepted = await security_store.risk_accepted_keys()

    # Exclude both ack'd findings AND active (non-expired) risk acceptances.
    # Without the risk-accepted exclusion, the dashboard gauge would stay
    # red even after the user explicitly documents acceptance of all
    # findings — which is exactly the inconsistency reported on the live
    # Slate (page Sécurité shows score 0/100 but Dashboard stays at 0/25).
    def _is_triaged(cve_id: str, pkg: str) -> bool:
        key = (cve_id, pkg)
        if key in acked:
            return True
        risk = risk_accepted.get(key)
        return risk is not None and not risk["expired"]

    cve_ids = [
        f.cve_id
        for f in findings
        if f.cve_id and not _is_triaged(f.cve_id, f.package_name)
    ]
    if not cve_ids:
        return HardeningCheck(
            name=name,
            points=_EXPLOIT_MAX_POINTS,
            max_points=_EXPLOIT_MAX_POINTS,
            status="ready",
            note=(
                "aucune CVE ouverte — "
                f"{len(acked)} ack'd, {sum(1 for v in risk_accepted.values() if not v['expired'])} risque(s) accepté(s)"
            ),
        )

    enrichments = await exploit_enricher.load_for_cves(cve_ids)
    crit = high = med = kev_count = msf_count = 0
    for enr in enrichments.values():
        if enr.priority_score >= 80:
            crit += 1
        elif enr.priority_score >= 60:
            high += 1
        elif enr.priority_score >= 40:
            med += 1
        if enr.kev is not None:
            kev_count += 1
        if enr.metasploit_modules:
            msf_count += 1

    penalty = (
        crit * _EXPLOIT_PENALTY_CRITICAL
        + high * _EXPLOIT_PENALTY_HIGH
        + med * _EXPLOIT_PENALTY_MEDIUM
    )
    points = max(_EXPLOIT_MAX_POINTS - penalty, 0)

    # Unenriched CVEs (e.g. enrichment hasn't run yet) skew the picture
    # downward. Flag the gap honestly in the note.
    unenriched = len(cve_ids) - len(enrichments)
    triaged_count = len(acked) + sum(
        1 for v in risk_accepted.values() if not v["expired"]
    )
    detail = (
        f"{crit} priority critical · {high} high · {med} medium ouverte(s) "
        f"(KEV={kev_count}, msf={msf_count})"
    )
    if triaged_count:
        detail += f" · {triaged_count} triée(s) exclues"
    if unenriched:
        detail += f" — {unenriched} CVE pas encore enrichies"
    return HardeningCheck(
        name=name,
        points=points,
        max_points=_EXPLOIT_MAX_POINTS,
        status="ready",
        note=detail,
    )


async def compute_hardening(
    slate: SlateClient,
    ssh: SlateSSH | None = None,
    *,
    security_store: SecurityStore | None = None,
    exploit_enricher: ExploitEnricher | None = None,
    device_id: int | None = None,
    adguard_manager: AdGuardManager | None = None,
) -> HardeningReport:
    """Run every check and aggregate. Resilient to Slate being unreachable.

    Each Slate RPC is hit at most once thanks to `_Snapshot` pre-fetching.
    """
    checks: list[HardeningCheck] = []
    reachable = True
    try:
        snap = await _build_snapshot(slate)
        checks.append(_fw_check(snap))
        checks.append(_init_check(snap))
        checks.append(_https_check(snap))
        checks.append(_admin_lan_check(snap))
        checks.append(_wan_rules_check(snap))
        checks.append(_service_check(
            snap, "tailscale", "Tailscale (canal admin de secours)", 10,
            "actif", "inactif — recommandé pour ne pas se locker",
        ))
        checks.append(_service_check(
            snap, "adguard", "AdGuard service actif", 5,
            "filtre DNS contre malware+tracking",
            "désactivé — activer pour bloquer trackers/domaines malicieux",
        ))
        checks.append(_wan_online_check(snap))
        checks.append(_temp_check(snap))
        checks.append(_ipv6_check(snap))
    except SlateUnreachableError as exc:
        logger.warning("hardening.slate_unreachable", error=str(exc))
        reachable = False

    if ssh is not None:
        checks.append(await _ssh_check_key_only(ssh))
        checks.append(await _ssh_check_upnp_off(ssh))
        checks.append(await _ssh_check_memory(ssh))
        checks.append(await _ssh_check_screen_lock(ssh))
    else:
        checks.append(HardeningCheck(
            name="SSH key-only auth", points=0, max_points=15,
            status="needs_probe", note="canal SSH désactivé",
        ))
        checks.append(HardeningCheck(
            name="UPnP désactivé", points=0, max_points=5,
            status="needs_probe", note="canal SSH désactivé",
        ))
        checks.append(HardeningCheck(
            name="Mémoire disponible (≥ 200 MB)", points=0, max_points=10,
            status="needs_probe", note="canal SSH désactivé",
        ))
        checks.append(HardeningCheck(
            name="PIN écran tactile", points=0, max_points=10,
            status="needs_probe", note="canal SSH désactivé",
        ))

    checks.append(await _luci_https_check(ssh))
    checks.append(await _wifi_isolation_check(ssh))
    checks.append(await _admin_ip_whitelist_check(ssh))
    checks.append(await _exploit_check(device_id, security_store, exploit_enricher))

    # Controller-local check: always runs, doesn't depend on Slate or SSH.
    # Critical because a weak controller password defeats every other check.
    checks.append(_controller_admin_password_check())

    # AdGuard-side DNS protection (DNSSEC + DoH/VPN bypass blocklist).
    # Hits AdGuard REST directly (one config read + one filters list).
    checks.extend(await _adguard_dns_protection_check(adguard_manager))

    score = sum(c.points for c in checks)
    max_score = sum(c.max_points for c in checks)
    return HardeningReport(
        score=score,
        max_score=max_score,
        reachable=reachable,
        checks=checks,
    )
