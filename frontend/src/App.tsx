import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
// Login & Dashboard stay eager — they're on the critical path right after
// authentication, the first thing every user sees.
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";

// Everything else is code-split. Each route is a separate chunk loaded on
// navigation, dropping the initial bundle from ~500 KB to ~150 KB and
// keeping heavy bits (SecurityCharts ~22 KB + Vulnerabilities + MITRE
// dataset) out of the first paint of /dashboard.
const Devices = lazy(() => import("./pages/Devices"));
const SecurityHub = lazy(() => import("./pages/security/SecurityHub"));
const SecurityHardening = lazy(() => import("./pages/security/Hardening"));
const SecurityTorAudit = lazy(() => import("./pages/security/TorAudit"));
const Vulnerabilities = lazy(() => import("./pages/security/Vulnerabilities"));
const SecurityTailscaleAudit = lazy(
  () => import("./pages/security/TailscaleAudit"),
);
const Profiles = lazy(() => import("./pages/Profiles"));
const ProfileForm = lazy(() => import("./pages/ProfileForm"));
const Wifi = lazy(() => import("./pages/Wifi"));
const Networks = lazy(() => import("./pages/Networks"));
const NetworkInterfaces = lazy(
  () => import("./pages/networks/Interfaces"),
);
const NetworkDiagnostic = lazy(
  () => import("./pages/networks/Diagnostic"),
);
const SlateScreen = lazy(() => import("./pages/SlateScreen"));
const ProtonVPN = lazy(() => import("./pages/ProtonVPN"));
const Tailscale = lazy(() => import("./pages/Tailscale"));
const AdGuard = lazy(() => import("./pages/AdGuard"));
const ProtectionDns = lazy(() => import("./pages/protection/Dns"));
const ProtectionFirewall = lazy(
  () => import("./pages/protection/Firewall"),
);
// Tor lives under /networks/* because it's a routing layer, not a
// protection — it sits next to Interfaces / Diagnostic / Réseaux / Radio.
const NetworksTor = lazy(() => import("./pages/networks/Tor"));
const NetworksRadio = lazy(() => import("./pages/networks/Radio"));
const SecurityAirWatch = lazy(() => import("./pages/security/AirWatch"));
const SettingsAppearance = lazy(() => import("./pages/settings/Appearance"));
const SettingsHub = lazy(() => import("./pages/settings/SettingsHub"));
const SettingsSshKey = lazy(() => import("./pages/settings/SshKey"));
const SettingsConnectivity = lazy(
  () => import("./pages/settings/Connectivity"),
);
const SettingsCommunication = lazy(
  () => import("./pages/settings/Communication"),
);
const SettingsTailnetAdmin = lazy(
  () => import("./pages/settings/TailnetAdmin"),
);
const SettingsAgent = lazy(() => import("./pages/settings/Agent"));
const SettingsControllerHttps = lazy(
  () => import("./pages/settings/ControllerHttps"),
);
const SettingsInternalCa = lazy(
  () => import("./pages/settings/InternalCa"),
);
const SettingsSetupStatus = lazy(
  () => import("./pages/settings/SetupStatus"),
);

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedRoute />}>
        {/* Layout uses Outlet internally; Suspense around the Outlet sits
            in Layout itself so the page chrome (sidebar, badge) stays
            visible while the lazy chunk loads. */}
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/security" element={<SecurityHub />} />
          <Route path="/security/hardening" element={<SecurityHardening />} />
          <Route path="/security/tor-audit" element={<SecurityTorAudit />} />
          <Route path="/security/vulnerabilities" element={<Vulnerabilities />} />
          <Route path="/security/tailscale" element={<SecurityTailscaleAudit />} />
          <Route path="/profiles" element={<Profiles />} />
          <Route path="/profiles/new" element={<ProfileForm />} />
          <Route path="/profiles/:name/edit" element={<ProfileForm />} />
          <Route path="/wifi" element={<Wifi />} />
          <Route path="/networks" element={<Networks />} />
          <Route
            path="/networks/interfaces"
            element={<NetworkInterfaces />}
          />
          <Route
            path="/networks/diagnostic"
            element={<NetworkDiagnostic />}
          />
          <Route path="/slate-screen" element={<SlateScreen />} />
          <Route path="/vpn/proton" element={<ProtonVPN />} />
          <Route path="/vpn/tailscale" element={<Tailscale />} />
          <Route path="/protection/adguard" element={<AdGuard />} />
          <Route path="/protection/dns" element={<ProtectionDns />} />
          <Route
            path="/protection/firewall"
            element={<ProtectionFirewall />}
          />
          <Route path="/networks/tor" element={<NetworksTor />} />
          <Route path="/networks/radio" element={<NetworksRadio />} />
          <Route path="/security/air-watch" element={<SecurityAirWatch />} />
          <Route path="/settings/appearance" element={<SettingsAppearance />} />
          <Route path="/settings" element={<SettingsHub />} />
          <Route path="/settings/ssh-key" element={<SettingsSshKey />} />
          <Route path="/settings/connectivity" element={<SettingsConnectivity />} />
          <Route path="/settings/tailnet-admin" element={<SettingsTailnetAdmin />} />
          <Route path="/settings/communication" element={<SettingsCommunication />} />
          <Route path="/settings/agent" element={<SettingsAgent />} />
          <Route
            path="/settings/controller-https"
            element={<SettingsControllerHttps />}
          />
          <Route
            path="/settings/internal-ca"
            element={<SettingsInternalCa />}
          />
          <Route
            path="/settings/setup-status"
            element={<SettingsSetupStatus />}
          />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
