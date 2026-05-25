"""Profile application — Phase 2b dry-run.

For a given Profile, compute the list of operations we WOULD perform on the
Slate to materialize it. Nothing is executed against the device. The output
is consumed by the UI so the user can review what an activation actually
entails before we flip on real execution in a follow-up phase.

Each subsystem returns a small list of `PlanStep`. Steps carry:
  - the target subsystem (vpn / dns / firewall / wifi / adguard / tor / tailscale)
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
        # DNS protection is no longer profile-driven — see Networks page.
        steps.extend(self._plan_firewall(profile))
        steps.extend(await self._plan_wifi(profile))
        steps.extend(self._plan_adguard(profile))
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
            steps.append(
                PlanStep(
                    subsystem="wifi",
                    action_kind="uci",
                    summary=f"{verb} SSID '{ssid.ssid_name}' ({ssid.band} · {ssid.security})",
                    note=(
                        f"uci set wireless.<iface>.disabled={'0' if ref.enabled else '1'}, "
                        f"puis `wifi reload`. Bridge: {ssid.network_slug}. "
                        f"Client-iso: {ssid.client_isolation}."
                    ),
                    target_values={
                        "slug": ssid.slug,
                        "ssid_name": ssid.ssid_name,
                        "band": ssid.band,
                        "network": ssid.network_slug,
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

    def _plan_adguard(self, profile: Profile) -> list[PlanStep]:
        ag = profile.adguard
        if not ag.enabled:
            return [
                PlanStep(
                    subsystem="adguard",
                    action_kind="service",
                    summary="AdGuard: arrêter le service",
                    note="service adguardhome stop",
                    readiness="ready",
                )
            ]
        steps = [
            PlanStep(
                subsystem="adguard",
                action_kind="service",
                summary="AdGuard: démarrer le service",
                note="service adguardhome start (RPC `adguardhome.get_config` confirmé OK).",
                readiness="ready",
            )
        ]
        if ag.lists:
            steps.append(
                PlanStep(
                    subsystem="adguard",
                    action_kind="rpc",
                    summary=f"Pousser les blocklists: {', '.join(ag.lists)}",
                    note="RPC adguardhome.set_filters (à valider).",
                    target_values={"lists": ag.lists},
                    readiness="needs_probe",
                )
            )
        return steps

    def _plan_tor(self, profile: Profile) -> list[PlanStep]:
        tor = profile.tor
        if not tor.enabled:
            return [
                PlanStep(
                    subsystem="tor",
                    action_kind="service",
                    summary="Tor: arrêter le service",
                    note="service tor stop",
                    readiness="ready",
                )
            ]
        steps = [
            PlanStep(
                subsystem="tor",
                action_kind="service",
                summary="Tor: démarrer le service (transparent proxy)",
                note="RPC tor.set_config + service tor restart.",
                target_values={"enabled": True, "bridge": tor.bridge},
                readiness="needs_probe",
            )
        ]
        if tor.bridge:
            steps.append(
                PlanStep(
                    subsystem="tor",
                    action_kind="uci",
                    summary="Activer obfs4 bridges (Tor non identifiable)",
                    note="Nécessite paquet `obfs4proxy` + liste de bridges configurée.",
                    readiness="needs_probe",
                )
            )
        return steps

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
            steps.append(
                PlanStep(
                    subsystem="tailscale",
                    action_kind="uci",
                    summary="Restreindre l'accès au tailnet admin uniquement",
                    note="ACL Tailscale + règle firewall: drop tout trafic non-admin sur l'iface ts.",
                    readiness="needs_probe",
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
