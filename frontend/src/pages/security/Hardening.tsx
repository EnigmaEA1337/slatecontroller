import { ShieldAlert } from "lucide-react";

import HardeningAuditPanel from "@/components/HardeningAuditPanel";
import { useT } from "@/lib/i18n";

export default function SecurityHardening() {
  const t = useT();
  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">
            {t("security.hardening_title").toUpperCase()}
          </h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          {t("security.hardening_subtitle")}
        </p>
      </div>
      <HardeningAuditPanel />
    </div>
  );
}
