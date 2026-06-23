"""Profile application — dry-run preview.

For a given Profile, compute the list of operations the slate-ctrl agent
would perform on the Slate to materialize it. Nothing is executed against
the device. The output is consumed by the UI's PlanModal so the operator
can review what an activation actually entails before pressing Activer.

Mapping to the agent (see `backend/app/slate_agent/scripts/handlers/`) :
  network.sh    → catalog reconcile (bridges, DHCP, zones, SC_FR_NET_*,
                  SC_FR_FWD_*) — global state, planned as one envelope step
  firewall.sh   → lockdown / wan forward / leak rules / geoip / block_*
  wifi.sh       → catalog-driven layout + mwctl no_bcn + ip link toggles
  radio.sh      → per-band channel / htmode / txpower (radio settings,
                  global state — planned as one envelope step)
  tor.sh        → daemon state + per-network routing (per-NetworkRow)
  tailscale.sh  → daemon up/down + admin_only whitelist (SC_FR_TS_ADMIN_*)
  vpn.sh        → wireguard client up/down + kill-switch
  adguard.sh    → daemon start/stop (no per-profile filterlists anymore)
  screen.sh     → one-shot loading overlay during the apply
  wallpaper.sh  → push the profile's `home` + `lock` wallpapers

Each subsystem returns a small list of `PlanStep`. Steps carry :
  - the target subsystem
  - the kind of action (uci / service / noop)
  - target values (what we'd push)
  - readiness (ready / skipped / blocker)
  - a human note in French

The `needs_probe` readiness was a Phase 2 placeholder for RPC endpoints we
hadn't validated. The agent path replaced that with shell handlers so any
new code here should use `ready` unless a real prerequisite is missing.
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
    "radio",
    "network",
    "adguard",
    "tor",
    "tailscale",
    "screen",
    "wallpaper",
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
        # Order mirrors the agent's actual run order in scripts/slate-ctrl :
        #   screen → network → firewall → wifi → vpn → tor → tailscale →
        #   wallpaper. We surface screen first and wallpaper last so the
        #   plan reads like a timeline of what the operator would see.
        steps: list[PlanStep] = []
        steps.extend(self._plan_screen(profile))
        steps.extend(self._plan_network())
        steps.extend(self._plan_radio())
        steps.extend(self._plan_firewall(profile))
        steps.extend(self._plan_adguard())
        # DNS protection + AdGuard filterlists removed from the profile model
        # — they live on each NetworkRow (Networks page → per-network DNS
        # protection drives AdGuard's persistent-clients REST API). The
        # adguard.sh handler only deals with the daemon start/stop now.
        steps.extend(await self._plan_wifi(profile))
        steps.extend(await self._plan_vpn(profile))
        steps.extend(self._plan_tor())
        steps.extend(self._plan_tailscale(profile))
        steps.extend(self._plan_wallpaper(profile))
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
                    "vpn.sh handler — pousser la config (clé privée + peer + "
                    "endpoint) dans /etc/config/network. Désactive les autres "
                    "clients en sortie pour éviter le dual-tunnel."
                ),
                target_values={"type": vpn.type, "client_name": vpn.client},
                readiness="ready" if vpn.type == "wireguard" else "needs_probe",
            )
        )
        steps.append(
            PlanStep(
                subsystem="vpn",
                action_kind="service",
                summary=f"Activer la connexion {vpn.client}",
                note=(
                    "ifup <ifname> du client WireGuard via netifd. OpenVPN reste "
                    "à câbler (pas dans la roadmap actuelle)."
                ),
                readiness="ready" if vpn.type == "wireguard" else "needs_probe",
            )
        )
        if vpn.kill_switch:
            steps.append(
                PlanStep(
                    subsystem="vpn",
                    action_kind="uci",
                    summary="Kill-switch firewall : DROP si oif != client VPN",
                    note=(
                        "Ajoute une règle SC_FR_KS_WAN_DROP : si le tunnel tombe, "
                        "le trafic LAN→WAN est bloqué. Couplé à block_all_outbound "
                        "pour un fail-closed strict."
                    ),
                    target_values={"kill_switch": True},
                    readiness="ready",
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
                    summary="Mode lockdown : wan forward=REJECT + activer les leak-rules",
                    note=(
                        "uci set firewall.@zone[wan].forward=REJECT + enable=1 sur "
                        "lan/guest/wgserver/ovpnserver_drop_leaked_dns + leak_adgdns. "
                        "fw3 reload (background, 1s delay) après commit."
                    ),
                    target_values={"lockdown": True},
                    readiness="ready",
                )
            )
        else:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="uci",
                    summary="wan forward=ACCEPT (mode standard)",
                    note=(
                        "Lockdown OFF : restaure la wan zone à forward=ACCEPT. "
                        "Les leak-rules sont laissées comme l'opérateur les a configurées "
                        "(page Hardening), on ne les force pas off."
                    ),
                    target_values={"lockdown": False},
                    readiness="ready",
                )
            )
        if fw.geoip_whitelist:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="noop",
                    summary=f"GeoIP whitelist demandée: {', '.join(fw.geoip_whitelist)}",
                    note=(
                        "V2 : nécessite iptables-mod-geoip + GeoLite2 DB (~12 MB, non "
                        "shippé par défaut). Le handler agent log et skip pour l'instant."
                    ),
                    target_values={"whitelist": fw.geoip_whitelist},
                    readiness="skipped",
                )
            )
        if fw.block_telemetry:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="noop",
                    summary="block_telemetry délégué à AdGuard",
                    note=(
                        "Les domaines de télémétrie vivent dans les filterlists "
                        "AdGuard (hagezi-tracker-radio, hagezi-pro). Pas une "
                        "responsabilité du firewall — handler log seulement."
                    ),
                    readiness="skipped",
                )
            )
        if fw.block_all_outbound:
            steps.append(
                PlanStep(
                    subsystem="firewall",
                    action_kind="uci",
                    summary="block_all_outbound : désactiver lan→wan forwarding",
                    note=(
                        "uci set firewall.@forwarding[lan→wan].enabled=0. "
                        "Très restrictif — couplé au kill-switch VPN."
                    ),
                    target_values={"deny_all_outbound": True},
                    readiness="ready",
                )
            )
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
            # wifi.sh handler flow per SSID :
            #   - up    : ip link up <ifname>   ; mwctl <ifname> set no_bcn 0
            #   - down  : mwctl <ifname> set no_bcn 1 ; ip link down <ifname>
            # Layout (ssid, encryption, network, mld) only changes on catalog
            # edits — pure profile activation stays reboot-free.
            action_desc = (
                "ip link up + mwctl no_bcn=0 (réarme le chip MTK)"
                if ref.enabled
                else "mwctl no_bcn=1 + ip link down (stoppe le beacon chip-level)"
            )
            steps.append(
                PlanStep(
                    subsystem="wifi",
                    action_kind="uci",
                    summary=f"{verb} SSID '{ssid.ssid_name}' ({band_label} · {ssid.security})",
                    note=(
                        f"{action_desc}. Bridge: br-{ref.network_slug}. "
                        f"Client-iso: {ssid.client_isolation}. Pas de reboot "
                        f"tant que le layout (ssid/enc/network) ne change pas."
                    ),
                    target_values={
                        "slug": ssid.slug,
                        "ssid_name": ssid.ssid_name,
                        "bands": list(ssid.bands),
                        "mlo": ssid.mlo,
                        "network": ref.network_slug,
                        "enabled": ref.enabled,
                    },
                    readiness="ready",
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

    def _plan_tor(self) -> list[PlanStep]:
        # Per-profile Tor was removed from the model. Daemon state lives in
        # TorSettings (DB, global), bridges in TorBridgeStore, per-network
        # routing modes on NetworkRow.tor_route_mode. The agent's tor.sh
        # handler reads the synced payload and reconciles all of these. We
        # surface a single envelope step here so the operator sees the
        # subsystem is touched without enumerating every network row.
        return [
            PlanStep(
                subsystem="tor",
                action_kind="service",
                summary="Tor : réconcilier daemon + per-network routing",
                note=(
                    "tor.sh handler — start/stop tor selon TorSettings.daemon_enabled, "
                    "pousse les bridges + exit-country, installe les DNAT REDIRECT "
                    "vers TransPort/DNSPort pour chaque NetworkRow.tor_route_mode = "
                    "transparent. Idempotent sur les modes inchangés."
                ),
                readiness="ready",
            )
        ]

    def _plan_tailscale(self, profile: Profile) -> list[PlanStep]:
        ts = profile.tailscale
        if not ts.enabled:
            return [
                PlanStep(
                    subsystem="tailscale",
                    action_kind="service",
                    summary="Tailscale : déconnecter",
                    note="tailscale down — termine la session tailnet sur le Slate.",
                    readiness="ready",
                )
            ]
        steps = [
            PlanStep(
                subsystem="tailscale",
                action_kind="service",
                summary="Tailscale : connecter + advertise subnets exposés",
                note=(
                    "tailscale up avec --advertise-routes calculé depuis la "
                    "colonne expose_to_tailnet de chaque NetworkRow (source de "
                    "vérité catalogue, pas une option profil). HA watchdog "
                    "réconcilié côté controller en parallèle."
                ),
                readiness="ready",
            )
        ]
        # Admin-only enforcement : depuis 2026-06-01 le flag profil est
        # ignoré côté sync — la whitelist (admin_ips non vide) suffit. On
        # le reflète ici pour que le plan corresponde au sync.
        from app.slate_agent.sync import ADMIN_PORTS_TCP
        ports = ", ".join(str(p) for p in ADMIN_PORTS_TCP)
        steps.append(
            PlanStep(
                subsystem="tailscale",
                action_kind="uci",
                summary=f"Tailnet admin whitelist : SC_FR_TS_ADMIN_* (TCP {ports})",
                note=(
                    "Le handler tailscale.sh purge tous les SC_FR_TS_ADMIN_* puis "
                    "re-crée ALLOW par IP whitelistée (Settings → Tailnet admin) + "
                    "DROP_ALL sur la plage tailnet. Whitelist vide = no-op (anti-"
                    "self-DoS). Indépendant du flag admin_only profil (retiré)."
                ),
                readiness="ready",
            )
        )
        return steps

    # ---------------------------- new envelope planners ---------------------- #

    def _plan_screen(self, profile: Profile) -> list[PlanStep]:
        return [
            PlanStep(
                subsystem="screen",
                action_kind="service",
                summary=f"LCD : afficher l'overlay 'loading {profile.name}'",
                note=(
                    "screen.sh handler — paint un PNG status via fb takeover, "
                    "puis restart gl_screen une fois l'apply terminé. Visible "
                    "pendant ~6 s sur l'écran tactile. Gated par "
                    "Settings.show_screen_messages."
                ),
                readiness="ready",
            )
        ]

    def _plan_network(self) -> list[PlanStep]:
        return [
            PlanStep(
                subsystem="network",
                action_kind="uci",
                summary="Réseaux : réconcilier le catalogue (bridges, DHCP, zones, forwardings)",
                note=(
                    "network.sh handler — upsert par réseau du catalogue : "
                    "device br-<slug>, interface (static/DHCPv6-PD), pool dhcp, "
                    "zone firewall + règles SC_FR_NET_<SLUG>_{DHCP,DNS,ICMP,"
                    "LUCI,SSH} + forwardings SC_FR_FWD_<SLUG>_TO_{WAN,<peer>}. "
                    "Orphan purger sur slate_ctrl_managed='1' uniquement. "
                    "Reload séquentiel : network → dnsmasq → firewall."
                ),
                readiness="ready",
            )
        ]

    def _plan_radio(self) -> list[PlanStep]:
        return [
            PlanStep(
                subsystem="radio",
                action_kind="uci",
                summary="Radio L1 : channel / htmode / txpower par bande (si configuré)",
                note=(
                    "radio.sh handler — réconcilie wireless.radio<N>.{channel,"
                    "htmode,txpower,country} depuis les RadioConfig store. "
                    "Optionnel : payload vide = handler no-op, le driver MTK "
                    "garde ses défauts ACS/EHT."
                ),
                readiness="ready",
            )
        ]

    def _plan_adguard(self) -> list[PlanStep]:
        return [
            PlanStep(
                subsystem="adguard",
                action_kind="service",
                summary="AdGuard Home : assurer le daemon démarré",
                note=(
                    "adguard.sh handler — start/stop le daemon AdGuard selon "
                    "présence de NetworkRows avec DNS protection activée. Pas "
                    "de filterlists profil-spécifiques (architecture per-network "
                    "via REST API persistent-clients, gérée par DnsProtectionManager)."
                ),
                readiness="ready",
            )
        ]

    def _plan_wallpaper(self, profile: Profile) -> list[PlanStep]:
        return [
            PlanStep(
                subsystem="wallpaper",
                action_kind="service",
                summary=f"LCD : pousser les wallpapers du profil '{profile.name}'",
                note=(
                    "wallpaper.sh handler — copie /etc/slate-controller/"
                    "wallpapers/<profile>_{home,lock}.png vers /etc/gl_screen/"
                    "wallpaper_{home,wake_display}.png puis restart gl_screen. "
                    "No-op si le profil n'a pas de wallpapers uploadés."
                ),
                readiness="ready",
            )
        ]

    def _plan_logging(self, profile: Profile) -> list[PlanStep]:
        # Logging is declared in the profile model (level, forward_to_siem)
        # but the agent has no `logging.sh` handler — the dispatcher's
        # handler loop is :
        #   screen network firewall wifi radio vpn tor adguard tailscale wallpaper
        # So we surface the intent honestly as a "skipped" step rather than
        # claim something happens. To wire this up later : add a
        # logging.sh handler that uci-sets system.@system[0].log_level (+
        # log_ip/log_proto/log_port for SIEM forward), and append `logging`
        # to the slate-ctrl dispatcher loop. This step keeps the UI showing
        # the user's declared intent so the gap is visible.
        log = profile.logging
        steps = [
            PlanStep(
                subsystem="logging",
                action_kind="noop",
                summary=f"Logging déclaré : level={log.level} (pas encore appliqué)",
                note=(
                    "Aucun handler logging.sh côté agent — le champ est "
                    "préservé dans le profil mais aucun uci set n'est poussé. "
                    "À câbler quand un cas d'usage SIEM se concrétisera."
                ),
                target_values={"level": log.level},
                readiness="skipped",
            )
        ]
        if log.forward_to_siem:
            steps.append(
                PlanStep(
                    subsystem="logging",
                    action_kind="noop",
                    summary="Forward syslog → SIEM demandé (non implémenté)",
                    note=(
                        "Champ profile.logging.forward_to_siem=true mais pas "
                        "de handler agent. Requiert SIEM_URL configuré + "
                        "logging.sh à écrire."
                    ),
                    readiness="skipped",
                )
            )
        return steps
