"""Profile application — Phase 2b dry-run.

For a given Profile, compute the list of operations we WOULD perform on the
Slate to materialize it. Nothing is executed against the device. The output
is consumed by the UI so the user can review what an activation actually
entails before we flip on real execution in a follow-up phase.

Each subsystem returns a small list of `PlanStep`. Steps carry:
  - the target subsystem (vpn / dns / firewall / wifi / tor / tailscale)
  - the kind of action (rpc / uci / service)
  - target values (what we'd push)
  - readiness (ready / needs_probe / skipped / blocker)
  - a human note in French

Readiness `needs_probe` flags subsystems where we haven't yet confirmed the
right RPC endpoint on firmware 4.8.4 (e.g. `wireguard_client.*` returned
"Method not found" during initial probe). Phase 2b real will close those.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from app.models.profile import Profile
from app.vpn.configs_store import VPNConfigNotFoundError, VPNConfigStore
from app.wifi.store import WifiSsidStore

Subsystem = Literal[
    "vpn",
    "dns",
    "firewall",
    "wifi",
    "adguard",
    "tor",
    "tailscale",
    "logging",
]
ActionKind = Literal["rpc", "uci", "service", "noop"]
Readiness = Literal["ready", "needs_probe", "skipped", "blocker"]


@dataclass
class PlanStep:
    subsystem: Subsystem
    action_kind: ActionKind
    summary: str  # short label for the UI
    note: str = ""  # longer human description
    target_values: dict = field(default_factory=dict)
    readiness: Readiness = "ready"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActivationPlan:
    profile_name: str
    steps: list[PlanStep]

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def blockers(self) -> list[PlanStep]:
        return [s for s in self.steps if s.readiness == "blocker"]

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "step_count": self.step_count,
            "has_blockers": bool(self.blockers),
            "steps": [s.to_dict() for s in self.steps],
        }


class ProfileApplier:
    """Compute (and eventually execute) the operations to materialize a Profile.

    Phase 2b dry-run: only `plan()` is implemented. `apply()` will come once
    we've validated the plan output against the real device for each subsystem.
    """

    def __init__(
        self,
        wifi_store: WifiSsidStore,
        vpn_config_store: VPNConfigStore,
    ) -> None:
        self._wifi = wifi_store
        self._vpn = vpn_config_store

    async def plan(self, profile: Profile) -> ActivationPlan:
        steps: list[PlanStep] = []
        steps.extend(await self._plan_vpn(profile))
        # DNS protection + AdGuard filtering are no longer profile-driven —
        # see Networks page (per-network DNS protections drive AdGuard
        # persistent clients).
        steps.extend(self._plan_firewall(profile))
        steps.extend(await self._plan_wifi(profile))
        steps.extend(self._plan_tor(profile))
        steps.extend(self._plan_tailscale(profile))
        steps.extend(self._plan_logging(profile))
        return ActivationPlan(profile_name=profile.name, steps=steps)

    # ---------------------------- subsystems ---------------------------- #

    async def _plan_vpn(self, profile: Profile) -> list[PlanStep]:
        vpn = profile.vpn
        if vpn.type == "none":
            return [
                PlanStep(
                    subsystem="vpn",
                    action_kind="noop",
                    summary="VPN: désactivé",
                    note="Aucun client VPN à activer pour ce profil.",
                    readiness="skipped",
                )
            ]
        if not vpn.client:
            return [
                PlanStep(
                    subsystem="vpn",
                    action_kind="noop",
                    summary=f"VPN {vpn.type}: nom de client manquant",
                    note="Le profil déclare un type VPN mais pas de `client`.",
                    readiness="blocker",
                )
            ]
        # Check the config exists in our store (only for wireguard for now)
        config_exists = False
        if vpn.type == "wireguard":
            try:
                await self._vpn.get(vpn.client)
                config_exists = True
            except VPNConfigNotFoundError:
                config_exists = False

        steps: list[PlanStep] = []
        if vpn.type == "wireguard" and not config_exists:
            steps.append(
                PlanStep(
                    subsystem="vpn",
                    action_kind="noop",
                    summary=f"WG config {vpn.client!r} introuvable dans le catalog",
                    note="Upload la config sur la page Proton VPN avant d'activer ce profil.",
                    readiness="blocker",
                )
            )
            return steps

        steps.append(
            PlanStep(
                subsystem="vpn",
                action_kind="uci",
                summary=f"Provisionner le client {vpn.type} '{vpn.client}'",
                note=(
                    "Pousser la config (clé privée + peer + endpoint) dans "
                    "/etc/config/network ou via le RPC client-side dédié."
                ),
                target_values={"type": vpn.type, "client_name": vpn.client},
                readiness="needs_probe",
            )
        )
        steps.append(
            PlanStep(
                subsystem="vpn",
                action_kind="service",
                summary=f"Activer la connexion {vpn.client}",
                note="ifup wgclient0 (ou équivalent OpenVPN).",
                readiness="needs_probe",
            )
        )
        if vpn.kill_switch:
            steps.append(
                PlanStep(
                    subsystem="vpn",
                    action_kind="uci",
                    summary="Activer le kill-switch (firewall lock)",
                    note=(
                        "Bloquer tout trafic sortant si le tunnel tombe : règle firewall "
                        "qui DROP si oif != wgclient0."
                    ),
                    target_values={"kill_switch": True},
                    readiness="needs_probe",
                )
            )
        return steps

    # _plan_dns() removed: DNS protection is now per-network and managed
    # via the Networks page + DnsProtectionManager (AdGuard Clients API).

    def _plan_firewall(self, profile: Profile) -> list[PlanStep]:
        fw = profile.firewall
        steps: list[PlanStep] = []
        if fw.lockdown:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="uci",
                    summary="Activer le mode lockdown",
                    note=(
                        "Zone lan: input=ACCEPT, output=REJECT par défaut, autoriser uniquement "
                        "les services whitelistés (DNS, NTP, VPN tunnel)."
                    ),
                    target_values={"lockdown": True},
                    readiness="needs_probe",
                )
            )
        if fw.geoip_whitelist:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="uci",
                    summary=f"GeoIP whitelist: {', '.join(fw.geoip_whitelist)}",
                    note=(
                        "Nécessite le paquet `ipset` + une source GeoIP. "
                        "Règle: DROP par défaut, ACCEPT si dest_country IN whitelist."
                    ),
                    target_values={"whitelist": fw.geoip_whitelist},
                    readiness="needs_probe",
                )
            )
        if fw.block_telemetry:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="uci",
                    summary="Bloquer les domaines de télémétrie connus",
                    note="Liste pré-établie (Microsoft, Apple, Google, Samsung…) via dnsmasq blocklist.",
                    readiness="needs_probe",
                )
            )
        if fw.block_all_outbound:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="uci",
                    summary="DROP par défaut sur tout l'outbound",
                    note="Mode panic — n'autoriser QUE les whitelist explicites. Très restrictif.",
                    target_values={"deny_all_outbound": True},
                    readiness="needs_probe",
                )
            )
        if not steps:
            return [
                PlanStep(
                    subsystem="firewall",
                    action_kind="noop",
                    summary="Firewall: défauts (pas de durcissement)",
                    readiness="skipped",
                )
            ]
        return steps

    async def _plan_wifi(self, profile: Profile) -> list[PlanStep]:
        steps: list[PlanStep] = []
        catalog = {s.slug: s for s in await self._wifi.list_all()}
        for ref in profile.ssids:
            ssid = catalog.get(ref.slug)
            if ssid is None:
                steps.append(
                    PlanStep(
                        subsystem="wifi",
                        action_kind="noop",
                        summary=f"SSID '{ref.slug}': référencé mais introuvable dans le catalog",
                        note="Crée le SSID sur la page Wi-Fi ou supprime la référence du profil.",
                        readiness="blocker",
                    )
                )
                continue
            verb = "Activer" if ref.enabled else "Désactiver"
            band_label = (
                "MLO " + "/".join(ssid.bands) if ssid.mlo
                else "/".join(f"{b}GHz" for b in ssid.bands)
            )
            steps.append(
                PlanStep(
                    subsystem="wifi",
                    action_kind="uci",
                    summary=f"{verb} SSID '{ssid.ssid_name}' ({band_label} · {ssid.security})",
                    note=(
                        f"uci set wireless.<iface>.disabled={'0' if ref.enabled else '1'} "
                        f"sur toutes les ifaces matchant '{ssid.ssid_name}', "
                        f"puis `wifi reload`. Bridge: {ref.network_slug}. "
                        f"Client-iso: {ssid.client_isolation}."
                    ),
                    target_values={
                        "slug": ssid.slug,
                        "ssid_name": ssid.ssid_name,
                        "bands": list(ssid.bands),
                        "mlo": ssid.mlo,
                        "network": ref.network_slug,
                        "enabled": ref.enabled,
                    },
                    readiness="needs_probe",
                )
            )
        if not steps:
            return [
                PlanStep(
                    subsystem="wifi",
                    action_kind="noop",
                    summary="Wi-Fi: aucun SSID référencé par ce profil",
                    readiness="skipped",
                )
            ]
        return steps

    def _plan_tor(self, _profile: Profile) -> list[PlanStep]:
        # Per-profile Tor was removed from the model — the global daemon
        # switch + bridges + exit_country live in TorSettings (DB), and
        # routing decisions live on NetworkRow.tor_route_mode. No profile-
        # scoped Tor planning anymore. Kept as a stub so any caller that
        # still iterates _plan_* methods gets an empty list instead of an
        # AttributeError on the removed `profile.tor` field.
        return []

    def _plan_tailscale(self, profile: Profile) -> list[PlanStep]:
        ts = profile.tailscale
        if not ts.enabled:
            return [
                PlanStep(
                    subsystem="tailscale",
                    action_kind="service",
                    summary="Tailscale: déconnecter",
                    note="tailscale down",
                    readiness="needs_probe",
                )
            ]
        steps = [
            PlanStep(
                subsystem="tailscale",
                action_kind="service",
                summary="Tailscale: connecter",
                note="tailscale up (RPC tailscale.get_status confirmé OK).",
                readiness="needs_probe",
            )
        ]
        if ts.admin_only:
            # Source of truth for the port list lives in the sync layer
            # (single ADMIN_PORTS_TCP tuple). Import lazily to avoid
            # cycles between profiles ↔ slate_agent at module load.
            from app.slate_agent.sync import ADMIN_PORTS_TCP
            ports = ", ".join(str(p) for p in ADMIN_PORTS_TCP)
            steps.append(
                PlanStep(
                    subsystem="tailscale",
                    action_kind="uci",
                    summary=f"Restreindre l'admin tailnet aux IPs whitelistées (TCP {ports})",
                    note=(
                        "Génère SC_FR_TS_ADMIN_ALLOW_<ip> + SC_FR_TS_ADMIN_DROP_ALL "
                        "puis fw3 reload. La whitelist se gère dans Settings > "
                        "Tailnet admin IPs ; vide = no-op (auto-downgrade côté sync)."
                    ),
                    readiness="ready",
                )
            )
        return steps

    def _plan_logging(self, profile: Profile) -> list[PlanStep]:
        log = profile.logging
        steps = [
            PlanStep(
                subsystem="logging",
                action_kind="uci",
                summary=f"Niveau de log système: {log.level}",
                note="uci set system.@system[0].log_level=<level>",
                target_values={"level": log.level},
                readiness="ready",
            )
        ]
        if log.forward_to_siem:
            steps.append(
                PlanStep(
                    subsystem="logging",
                    action_kind="uci",
                    summary="Activer le forward syslog → SIEM externe",
                    note="uci set system.@system[0].log_ip + log_proto (+ port). Requiert SIEM_URL configuré.",
                    readiness="needs_probe",
                )
            )
        return steps
