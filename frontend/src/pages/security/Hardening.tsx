import { ShieldAlert } from "lucide-react";
import HardeningAuditPanel from "@/components/HardeningAuditPanel";

export default function SecurityHardening() {
  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">HARDENING DEVICE</h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          Niveau de durcissement du Slate — checks OpenWrt/GL.iNet contre le baseline pondéré.
        </p>
      </div>
      <HardeningAuditPanel />
    </div>
  );
}
