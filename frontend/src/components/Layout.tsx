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
  Router,
  Shield,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getActiveProfile } from "@/api/profiles";
import { useCurrentUser, useLogout } from "@/hooks/useAuth";
import { reliabilityShieldStyle } from "@/components/ReliabilityShield";
import DevicePicker from "@/components/DevicePicker";
import SlateConnectivityBadge from "@/components/SlateConnectivityBadge";
import { useSecurityReliability } from "@/hooks/useSecurityReliability";
import { useWallpaperBlobUrl } from "@/hooks/useWallpaper";
import { cn } from "@/lib/utils";

const topItems = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/devices", label: "Devices", icon: Router, end: false },
  { to: "/profiles", label: "Profils", icon: ShieldCheck, end: false },
  // Slate Screen renamed to "Remote Control" — same route for now,
  // becomes a full remote-screen-control surface in a future iteration.
  { to: "/slate-screen", label: "Remote Control", icon: MonitorSmartphone, end: false },
];

// New "Réseau" expandable group: groups all LAN-side surfaces (physical
// + logical) so the top-level nav stays tight. Interfaces is a fresh
// page added in this restructure ; the other two are moves from the
// top list (their URLs stay /wifi and /networks — no link rewrites).
const networkChildren = [
  { to: "/networks/interfaces", label: "Interfaces" },
  { to: "/networks", label: "Réseaux" },
  { to: "/wifi", label: "Wi-Fi" },
];

// "Audit" group — renamed from "Sécurité" so the label matches what
// the page actually does (hardening + CVE + Tailscale audit are all
// READ-ONLY postures, not active defenses). URLs stay /security/* to
// avoid breaking bookmarks.
const auditChildren = [
  { to: "/security/hardening", label: "Hardening" },
  { to: "/security/vulnerabilities", label: "Vulnérabilités" },
  { to: "/security/tailscale", label: "Tailscale Audit" },
];

const settingsChildren = [
  { to: "/settings/ssh-key", label: "SSH Keypair" },
  { to: "/settings/connectivity", label: "Connectivity" },
  { to: "/settings/communication", label: "Communication" },
  { to: "/settings/agent", label: "Agent local" },
];

const vpnChildren = [
  { to: "/vpn/proton", label: "Proton VPN" },
  { to: "/vpn/tailscale", label: "Tailscale" },
];

const protectionChildren = [
  { to: "/protection/adguard", label: "AdGuard" },
  { to: "/protection/dns", label: "DNS" },
];

export default function Layout() {
  const me = useCurrentUser();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();

  const isNetworkPath = (p: string) =>
    p.startsWith("/networks") || p.startsWith("/wifi");

  const [networkOpen, setNetworkOpen] = useState(() =>
    isNetworkPath(location.pathname),
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
        <div className="cyber-display cyber-glow mb-1 text-lg">CONTROLLER</div>
        <div className="cyber-hatch mb-8 mt-1 h-px w-full" />

        <nav className="flex-1 space-y-1">
          {topItems.map((item) => (
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
              <item.icon className="h-3.5 w-3.5" />
              {item.label}
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
              <span>Réseau</span>
              {networkOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {networkOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {networkChildren.map((child) => (
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
                    {child.label}
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
                    ? `Fiabilité Slate: ${reliability.percent}% — ${reliabilityStyle.label}`
                    : "Fiabilité Slate: indéterminée"
                }
              >
                <SecurityIcon className={cn("h-3.5 w-3.5", reliabilityStyle.text)} />
                <span>Audit</span>
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
                aria-label={securityOpen ? "Replier audit" : "Déplier audit"}
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
                {auditChildren.map((child) => (
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
                    {child.label}
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
              <span>VPN</span>
              {vpnOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {vpnOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {vpnChildren.map((child) => (
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
                    {child.label}
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
              <span>Protection</span>
              {protectionOpen ? (
                <ChevronDown className="ml-auto h-3 w-3" />
              ) : (
                <ChevronRight className="ml-auto h-3 w-3" />
              )}
            </button>

            {protectionOpen && (
              <div className="ml-3 mt-1 border-l border-[color:var(--color-cyber-border)] pl-2">
                {protectionChildren.map((child) => (
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
                    {child.label}
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
                  <span>Settings</span>
                </NavLink>
                <button
                  type="button"
                  onClick={() => setSettingsOpen((v) => !v)}
                  aria-expanded={settingsOpen}
                  aria-label={settingsOpen ? "Replier settings" : "Déplier settings"}
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
                  {settingsChildren.map((child) => (
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
                      {child.label}
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
          <div className="cyber-label pt-2">user</div>
          <div className="cyber-glow px-1 text-sm font-extrabold uppercase tracking-[0.15em]">
            {me.data?.username ?? "—"}
          </div>
          <button
            type="button"
            onClick={() => logout.mutate()}
            className="mt-2 flex w-full items-center gap-2 border border-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/8 hover:text-[color:var(--color-cyber-accent)]"
          >
            <LogOut className="h-3.5 w-3.5" />
            Déconnexion
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden">
        {/* Suspense around the route Outlet so the sidebar + connectivity
            badge stay visible while a lazy-loaded page chunk arrives. */}
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center p-12">
              <span className="cyber-label cyber-cursor text-xs uppercase tracking-[0.3em] text-[color:var(--color-cyber-muted)]">
                chargement…
              </span>
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
