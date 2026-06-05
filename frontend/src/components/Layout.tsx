import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Cable,
  ChevronDown,
  ChevronRight,
  Cog,
  Gauge,
  LogOut,
  MonitorSmartphone,
  Network,
  Radio,
  Router,
  Shield,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getActiveProfile } from "@/api/profiles";
import { useAdoptedDevice } from "@/hooks/useAdoptedDevice";
import { useCurrentUser, useLogout } from "@/hooks/useAuth";
import { reliabilityShieldStyle } from "@/components/ReliabilityShield";
import DevicePicker from "@/components/DevicePicker";
import LockoutBanner from "@/components/LockoutBanner";
import SlateConnectivityBadge from "@/components/SlateConnectivityBadge";
import { useSecurityReliability } from "@/hooks/useSecurityReliability";
import { useWallpaperBlobUrl } from "@/hooks/useWallpaper";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  labelKey: string;
  icon?: typeof Gauge;
  end?: boolean;
}

const TOP_ITEMS: NavItem[] = [
  { to: "/", labelKey: "nav.item_dashboard", icon: Gauge, end: true },
  { to: "/devices", labelKey: "nav.item_devices", icon: Router, end: false },
  { to: "/profiles", labelKey: "nav.item_profiles", icon: ShieldCheck, end: false },
  // Slate Screen renamed to "Remote Control" — same route for now,
  // becomes a full remote-screen-control surface in a future iteration.
  { to: "/slate-screen", labelKey: "nav.item_remote_control", icon: MonitorSmartphone, end: false },
];

// "Réseau" expandable group : surfaces LAN câblées et logiques.
const NETWORK_CHILDREN: NavItem[] = [
  { to: "/networks/interfaces", labelKey: "nav.item_interfaces" },
  { to: "/networks/diagnostic", labelKey: "nav.item_diagnostic" },
  { to: "/networks", labelKey: "nav.item_networks" },
  { to: "/wifi", labelKey: "nav.item_ssids" },
  { to: "/wifi/orphans", labelKey: "nav.item_ssids_orphans" },
  // Tor = couche de routage par-réseau (per-bridge transparent / SOCKS),
  // donc sa place est ici à côté des autres surfaces réseau — pas dans
  // "Protection".
  { to: "/networks/tor", labelKey: "nav.item_tor" },
];

// Air Wave : tout ce qui est RF / radio / OSINT WiFi. Sorti de "Réseau"
// pour avoir une section dédiée maintenant qu'on a 4 surfaces (config
// + carte + ambient + sessions de surveillance).
// Note : les URLs restent /networks/* pour ne pas casser les bookmarks ;
// seule la place dans la nav change.
const AIR_WAVE_CHILDREN: NavItem[] = [
  // Layer-1 config (channel / htmode / txpower / country) + scanner ponctuel.
  { to: "/networks/radio", labelKey: "nav.item_rf_scanner" },
  { to: "/networks/radio/map", labelKey: "nav.item_geo_map" },
  // Background scan loop : APScheduler job per band, ~6 KB/jour.
  { to: "/networks/ambient", labelKey: "nav.item_ambient" },
  // Sessions de surveillance nommées + timeline classifiée.
  { to: "/networks/surveillance", labelKey: "nav.item_surveillance" },
  { to: "/networks/pcap", labelKey: "nav.item_pcap" },
];

// "Audit" group — renamed from "Sécurité" so the label matches what
// the page actually does (hardening + CVE + Tailscale audit are all
// READ-ONLY postures, not active defenses). URLs stay /security/* to
// avoid breaking bookmarks.
const AUDIT_CHILDREN: NavItem[] = [
  { to: "/security/hardening", labelKey: "nav.item_hardening" },
  { to: "/security/vulnerabilities", labelKey: "nav.item_vulnerabilities" },
  { to: "/security/tailscale", labelKey: "nav.item_tailscale_audit" },
  { to: "/security/tor-audit", labelKey: "nav.item_tor_audit" },
  { to: "/security/air-watch", labelKey: "nav.item_air_watch" },
  { to: "/security/anti-theft", labelKey: "nav.item_anti_theft" },
];

const SETTINGS_CHILDREN: NavItem[] = [
  { to: "/settings/setup-status", labelKey: "nav.item_setup_status" },
  { to: "/settings/ssh-key", labelKey: "nav.item_ssh_keypair" },
  { to: "/settings/controller-https", labelKey: "nav.item_https_controller" },
  { to: "/settings/internal-ca", labelKey: "nav.item_internal_ca" },
  { to: "/settings/tailnet-admin", labelKey: "nav.item_tailnet_admin" },
  { to: "/settings/connectivity", labelKey: "nav.item_callback_urls" },
  { to: "/settings/communication", labelKey: "nav.item_communication" },
  { to: "/settings/agent", labelKey: "nav.item_local_agent" },
  // Pure client-side preference — palette day / night / auto + langue.
  { to: "/settings/appearance", labelKey: "nav.item_appearance" },
];

const VPN_CHILDREN: NavItem[] = [
  { to: "/vpn/proton", labelKey: "nav.item_proton_vpn" },
  { to: "/vpn/tailscale", labelKey: "nav.item_tailscale" },
];

const PROTECTION_CHILDREN: NavItem[] = [
  { to: "/protection/adguard", labelKey: "nav.item_adguard" },
  { to: "/protection/dns", labelKey: "nav.item_dns" },
  { to: "/protection/firewall", labelKey: "nav.item_firewall" },
];

// Routes that stay reachable BEFORE adoption — operator needs them to
// finish first-time setup. Everything else is hidden / redirected to
// /devices until at least one Slate is adopted.
const ONBOARDING_ALLOWLIST: ReadonlyArray<string> = [
  "/devices",
  "/settings",
];

function pathIsAllowedPreAdoption(path: string): boolean {
  return ONBOARDING_ALLOWLIST.some(
    (prefix) => path === prefix || path.startsWith(prefix + "/"),
  );
}

export default function Layout() {
  const t = useT();
  const me = useCurrentUser();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();

  // Onboarding gate : until the first Slate is adopted, the controller
  // can't talk to anything — dashboards, security scans, profile apply
  // all probe a device. Redirect to /devices so the user can finish
  // adoption ; allow /settings paths through (SSH keypair etc. are
  // adoption prereqs).
  const adopted = useAdoptedDevice();
  useEffect(() => {
    if (adopted.isLoading) return;
    if (adopted.hasAdopted) return;
    if (pathIsAllowedPreAdoption(location.pathname)) return;
    navigate("/devices", { replace: true });
  }, [adopted.isLoading, adopted.hasAdopted, location.pathname, navigate]);

  // Air Wave paths live under /networks/* historically but they belong
  // to their own section in the nav — so we explicitly exclude them
  // from `isNetworkPath` and have a dedicated `isAirWavePath`. This
  // keeps both top groups exclusive : the glow lights only the right
  // one when the user navigates to e.g. /networks/radio.
  const isAirWavePath = (p: string) =>
    p === "/networks/radio" ||
    p.startsWith("/networks/radio/") ||
    p.startsWith("/networks/ambient") ||
    p.startsWith("/networks/surveillance");
  const isNetworkPath = (p: string) =>
    (p.startsWith("/networks") || p.startsWith("/wifi")) &&
    !isAirWavePath(p);

  const [networkOpen, setNetworkOpen] = useState(() =>
    isNetworkPath(location.pathname),
  );
  const [airWaveOpen, setAirWaveOpen] = useState(() =>
    isAirWavePath(location.pathname),
  );
  const [vpnOpen, setVpnOpen] = useState(() =>
    location.pathname.startsWith("/vpn"),
  );
  const [protectionOpen, setProtectionOpen] = useState(() =>
    location.pathname.startsWith("/protection"),
  );
  const [securityOpen, setSecurityOpen] = useState(() =>
    location.pathname.startsWith("/security"),
  );
  const [settingsOpen, setSettingsOpen] = useState(() =>
    location.pathname.startsWith("/settings"),
  );
  useEffect(() => {
    if (isNetworkPath(location.pathname)) {
      setNetworkOpen(true);
    }
    if (isAirWavePath(location.pathname)) {
      setAirWaveOpen(true);
    }
    if (location.pathname.startsWith("/vpn")) {
      setVpnOpen(true);
    }
    if (location.pathname.startsWith("/protection")) {
      setProtectionOpen(true);
    }
    if (location.pathname.startsWith("/security")) {
      setSecurityOpen(true);
    }
    if (location.pathname.startsWith("/settings")) {
      setSettingsOpen(true);
    }
  }, [location.pathname]);

  if (me.isError) {
    navigate("/login", { replace: true });
  }

  const networkActive = isNetworkPath(location.pathname);
  const airWaveActive = isAirWavePath(location.pathname);
  const vpnActive = location.pathname.startsWith("/vpn");
  const protectionActive = location.pathname.startsWith("/protection");
  const securityActive = location.pathname.startsWith("/security");
  const settingsActive = location.pathname.startsWith("/settings");

  // Aggregated reliability — drives the sidebar shield icon color so the
  // user has at-a-glance posture awareness from any page.
  const reliability = useSecurityReliability();
  const reliabilityStyle = reliabilityShieldStyle(reliability.status);
  const SecurityIcon = reliabilityStyle.Icon;

  // Active profile drives the global wallpaper. We can't tell from a
  // listing query whether the active profile has a wallpaper, so we fetch
  // the lightweight "active" payload + the profile envelope for the flag.
  // The wallpaper hook handles the "no wallpaper" case gracefully (null).
  const activeQ = useQuery({
    queryKey: ["profiles", "active"],
    queryFn: getActiveProfile,
    staleTime: 30_000,
  });
  const wallpaperUrl = useWallpaperBlobUrl(
    activeQ.data?.active_name ?? null,
    // We don't have has_wallpaper from /active — try to fetch the wallpaper
    // unconditionally; the hook simply gets null on 404 which is harmless.
    !!activeQ.data?.active_name,
    "home",
    activeQ.data?.profile?.name ?? undefined,
  );

  return (
    <div className="relative flex min-h-screen">
      {wallpaperUrl && (
        // Behind everything, dim overlay on top to keep contrast readable.
        // Fixed positioning so the image stays put while content scrolls.
        <>
          <div
            className="pointer-events-none fixed inset-0 -z-20 bg-cover bg-center bg-no-repeat"
            style={{ backgroundImage: `url(${wallpaperUrl})` }}
          />
          <div className="pointer-events-none fixed inset-0 -z-10 bg-[color:var(--color-cyber-bg)]/85" />
        </>
      )}
      <aside className="hidden w-64 flex-col border-r border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/70 px-4 py-6 backdrop-blur md:flex">
        <div className="mb-2 flex items-center gap-2">
          <Terminal className="cyber-glow h-4 w-4" />
          <span className="text-[10px] uppercase tracking-[0.35em] text-[color:var(--color-cyber-muted)]">
            slate://
          </span>
        </div>
        <div className="cyber-display cyber-glow mb-1 text-lg">
          {t("nav.brand").toUpperCase()}
        </div>
        <div className="cyber-hatch mb-8 mt-1 h-px w-full" />

        <nav className="flex-1 space-y-1">
          {TOP_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                  isActive
                    ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                    : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
                )
              }
            >
              {item.icon && <item.icon className="h-3.5 w-3.5" />}
              {t(item.labelKey)}
            </NavLink>
          ))}

          {/* Réseau expandable group — physical interfaces + logical
              network bridges + Wi-Fi. Grouped here so the top-level
              nav doesn't get cluttered with 3 LAN-side surfaces. */}
          <div>
            <button
              type="button"
              onClick={() => setNetworkOpen((v) => !v)}
              className={cn(
                "group flex w-full items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                networkActive
                  ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                  : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
              )}
              aria-expanded={networkOpen}
            >
              <Cable className="h-3.5 w-3.5" />
              <span>{t("nav.section_network")}</span>
              {networkOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {networkOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {NETWORK_CHILDREN.map((child) => (
                  <NavLink
                    key={child.to}
                    to={child.to}
                    end={child.to === "/networks"}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition-all",
                        isActive
                          ? "cyber-glow"
                          : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                      )
                    }
                  >
                    <span className="text-[color:var(--color-cyber-accent)]">▸</span>
                    {t(child.labelKey)}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {/* Air Wave expandable group — RF / radio / WiFi OSINT.
              Distinct from "Réseau" (LAN-side wiring) because the layer-1
              surfaces have their own lifecycle (scans, sessions, classification). */}
          <div>
            <button
              type="button"
              onClick={() => setAirWaveOpen((v) => !v)}
              className={cn(
                "group flex w-full items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                airWaveActive
                  ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                  : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
              )}
              aria-expanded={airWaveOpen}
            >
              <Radio className="h-3.5 w-3.5" />
              <span>{t("nav.section_air_wave")}</span>
              {airWaveOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {airWaveOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {AIR_WAVE_CHILDREN.map((child) => (
                  <NavLink
                    key={child.to}
                    to={child.to}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition-all",
                        isActive
                          ? "cyber-glow"
                          : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                      )
                    }
                  >
                    <span className="text-[color:var(--color-cyber-accent)]">▸</span>
                    {t(child.labelKey)}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {/* Security expandable group — top-level entry navigates to hub */}
          <div>
            <div
              className={cn(
                "group flex w-full items-stretch border border-transparent text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                securityActive
                  ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                  : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
              )}
            >
              <NavLink
                to="/security"
                end
                className="flex flex-1 items-center gap-2 px-3 py-2"
                title={
                  reliability.percent !== null
                    ? t("nav.reliability_tooltip", {
                        percent: reliability.percent,
                        label: reliabilityStyle.label,
                      })
                    : t("nav.reliability_unknown")
                }
              >
                <SecurityIcon className={cn("h-3.5 w-3.5", reliabilityStyle.text)} />
                <span>{t("nav.section_audit")}</span>
                {reliability.percent !== null && (
                  <span
                    className={cn(
                      "ml-auto font-mono text-[9px]",
                      reliabilityStyle.text,
                    )}
                  >
                    {reliability.percent}%
                  </span>
                )}
              </NavLink>
              <button
                type="button"
                onClick={() => setSecurityOpen((v) => !v)}
                aria-expanded={securityOpen}
                aria-label={
                  securityOpen
                    ? `${t("nav.collapse")} ${t("nav.section_audit")}`
                    : `${t("nav.expand")} ${t("nav.section_audit")}`
                }
                className="flex items-center px-2"
              >
                {securityOpen ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
              </button>
            </div>

            {securityOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {AUDIT_CHILDREN.map((child) => (
                  <NavLink
                    key={child.to}
                    to={child.to}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition-all",
                        isActive
                          ? "cyber-glow"
                          : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                      )
                    }
                  >
                    <span className="text-[color:var(--color-cyber-accent)]">▸</span>
                    {t(child.labelKey)}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {/* VPN expandable group */}
          <div>
            <button
              type="button"
              onClick={() => setVpnOpen((v) => !v)}
              className={cn(
                "group flex w-full items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                vpnActive
                  ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                  : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
              )}
              aria-expanded={vpnOpen}
            >
              <Network className="h-3.5 w-3.5" />
              <span>{t("nav.section_vpn")}</span>
              {vpnOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {vpnOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {VPN_CHILDREN.map((child) => (
                  <NavLink
                    key={child.to}
                    to={child.to}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition-all",
                        isActive
                          ? "cyber-glow"
                          : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                      )
                    }
                  >
                    <span className="text-[color:var(--color-cyber-accent)]">▸</span>
                    {t(child.labelKey)}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {/* Protection expandable group */}
          <div>
            <button
              type="button"
              onClick={() => setProtectionOpen((v) => !v)}
              className={cn(
                "group flex w-full items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                protectionActive
                  ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                  : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
              )}
              aria-expanded={protectionOpen}
            >
              <Shield className="h-3.5 w-3.5" />
              <span>{t("nav.section_protection")}</span>
              {protectionOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {protectionOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {PROTECTION_CHILDREN.map((child) => (
                  <NavLink
                    key={child.to}
                    to={child.to}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition-all",
                        isActive
                          ? "cyber-glow"
                          : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                      )
                    }
                  >
                    <span className="text-[color:var(--color-cyber-accent)]">▸</span>
                    {t(child.labelKey)}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          <div className="mt-6 border-t border-[color:var(--color-cyber-border)] pt-3">
            {/* Settings expandable group — top-level entry navigates to hub */}
            <div>
              <div
                className={cn(
                  "group flex w-full items-stretch border border-transparent text-[11px] font-bold uppercase tracking-[0.2em] transition-all",
                  settingsActive
                    ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
                    : "text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-border-strong)] hover:bg-[color:var(--color-cyber-surface)] hover:text-[color:var(--color-cyber-fg)]",
                )}
              >
                <NavLink
                  to="/settings"
                  end
                  className="flex flex-1 items-center gap-2 px-3 py-2"
                >
                  <Cog className="h-3.5 w-3.5" />
                  <span>{t("nav.section_settings")}</span>
                </NavLink>
                <button
                  type="button"
                  onClick={() => setSettingsOpen((v) => !v)}
                  aria-expanded={settingsOpen}
                  aria-label={
                    settingsOpen
                      ? `${t("nav.collapse")} ${t("nav.section_settings")}`
                      : `${t("nav.expand")} ${t("nav.section_settings")}`
                  }
                  className="flex items-center px-2"
                >
                  {settingsOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                </button>
              </div>
              {settingsOpen && (
                <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                  {SETTINGS_CHILDREN.map((child) => (
                    <NavLink
                      key={child.to}
                      to={child.to}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition-all",
                          isActive
                            ? "cyber-glow"
                            : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                        )
                      }
                    >
                      <span className="text-[color:var(--color-cyber-accent)]">▸</span>
                      {t(child.labelKey)}
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          </div>
        </nav>

        <div className="mt-auto space-y-2 border-t border-[color:var(--color-cyber-border)] pt-4">
          {/* Device selector — auto-hides for single-device setups, shows
              a dropdown once a 2nd device is adopted. Switching the
              selected device adds `?device=<slug>` to every backend
              request via the axios interceptor. */}
          <DevicePicker />
          {/* Live badge — quel chemin réseau le contrôleur utilise pour
              joindre le Slate (bascule auto LAN ↔ Tailscale ↔ custom). */}
          <SlateConnectivityBadge />
          <div className="cyber-label pt-2">{t("nav.user")}</div>
          <div className="cyber-glow px-1 text-sm font-extrabold uppercase tracking-[0.15em]">
            {me.data?.username ?? "—"}
          </div>
          <button
            type="button"
            onClick={() => logout.mutate()}
            className="mt-2 flex w-full items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/8 hover:text-[color:var(--color-cyber-accent)]"
          >
            <LogOut className="h-3.5 w-3.5" />
            {t("nav.logout")}
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden">
        {/* Global PIN-verifier lockout banner — renders only when the
            controller-side lockout is active. The native gl_screen
            touchscreen lockout (5 min after N misses) lives on the
            Slate itself and isn't observable from here. */}
        <LockoutBanner />
        {/* Suspense around the route Outlet so the sidebar + connectivity
            badge stay visible while a lazy-loaded page chunk arrives. */}
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center p-12">
              <span className="cyber-label cyber-cursor text-xs uppercase tracking-[0.3em] text-[color:var(--color-cyber-muted)]">
                {t("nav.loading")}
              </span>
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </main>

      {/* The floating "Apply" pill was removed — too coarse, too
          ambiguous (was it pending or always there?). Per-control inline
          apply is the new direction, see the per-page Save buttons. */}
    </div>
  );
}
