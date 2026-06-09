/**
 * Network Diagnostic — dedicated page wrapping the live L2/L3 diagnostic
 * panel. Moved out of the /networks cards view (it was an inline section
 * there) into its own submenu entry under "Réseau" so the heavy on-demand
 * SSH probe (~25s) doesn't share screen space with the network catalog.
 */

import NetworkDiagPanel from "@/components/NetworkDiagPanel";
import { useT } from "@/lib/i18n";

export default function NetworkDiagnostic() {
  const t = useT();
  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          {t("nav.section_network")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("net_diagnostic.title").toUpperCase()}
        >
          {t("net_diagnostic.title").toUpperCase()}
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("net_diagnostic.subtitle")}
        </p>
      </header>

      <NetworkDiagPanel />
    </div>
  );
}
