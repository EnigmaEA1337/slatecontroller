// WiFi orphan cleanup page (Phase 2). Lists every wifi-iface/wifi-mld
// section on the Slate that isn't owned by the controller, with per-row
// delete + bulk delete.

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Trash2,
  Wifi,
} from "lucide-react";

import {
  type WifiOrphan,
  cleanupWifiOrphans,
  deleteWifiOrphan,
  listWifiOrphans,
} from "@/api/wifi-orphans";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

export default function WifiOrphansPage() {
  const t = useT();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["wifi", "orphans"],
    queryFn: () => listWifiOrphans(),
    refetchInterval: 30_000,
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());

  const orphans = list.data ?? [];

  // Reset selection when the list changes (after a successful delete the
  // set would point to ghosts otherwise).
  const orphanIds = useMemo(
    () => orphans.map((o) => o.section).join("|"),
    [orphans],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useMemo(() => setSelected(new Set()), [orphanIds]);

  const delOne = useMutation({
    mutationFn: (section: string) => deleteWifiOrphan(section),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wifi", "orphans"] }),
  });
  const [lastBulk, setLastBulk] = useState<Record<string, string> | null>(null);
  const delBulk = useMutation({
    mutationFn: (sections: string[]) => cleanupWifiOrphans(sections),
    onSuccess: (result) => {
      setLastBulk(result);
      qc.invalidateQueries({ queryKey: ["wifi", "orphans"] });
    },
  });

  const toggleAll = () => {
    if (selected.size === orphans.length) setSelected(new Set());
    else setSelected(new Set(orphans.map((o) => o.section)));
  };
  const toggleOne = (section: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });

  return (
    <div className="space-y-4">
      <header className="cyber-label flex items-center gap-2">
        <Wifi className="h-3 w-3" /> {t("wifi_orphans.title")}
      </header>

      <p className="text-xs text-[color:var(--color-cyber-muted)] max-w-3xl">
        {t("wifi_orphans.subtitle")}
      </p>

      <section className="cyber-card p-3">
        <header className="cyber-label text-[10px] mb-2 flex items-center justify-between">
          <span>
            {orphans.length} section{orphans.length > 1 ? "s" : ""} orphelin
            {orphans.length > 1 ? "es" : "e"}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => list.refetch()}
              className="cyber-button-ghost px-2 py-0.5 text-[10px]"
            >
              <RefreshCw
                className={cn("h-3 w-3", list.isFetching && "animate-spin")}
              />
            </button>
            <button
              onClick={() => {
                if (
                  confirm(
                    `Supprimer ${selected.size} section(s) sélectionnée(s) ? Action irréversible.`,
                  )
                ) {
                  delBulk.mutate(Array.from(selected));
                }
              }}
              disabled={selected.size === 0 || delBulk.isPending}
              className="cyber-button px-3 py-1 text-[10px]"
            >
              {delBulk.isPending
                ? "…"
                : `supprimer la sélection (${selected.size})`}
            </button>
          </div>
        </header>

        {orphans.length === 0 ? (
          <p className="text-xs text-[color:var(--color-cyber-muted)]">
            ✓ Aucun orphelin détecté — le Slate est propre.
          </p>
        ) : (
          <table className="w-full font-mono text-[11px]">
            <thead>
              <tr className="text-[color:var(--color-cyber-muted)] text-left">
                <th className="px-2 py-1 w-8">
                  <input
                    type="checkbox"
                    checked={selected.size === orphans.length}
                    onChange={toggleAll}
                  />
                </th>
                <th className="px-2 py-1">section</th>
                <th className="px-2 py-1">type</th>
                <th className="px-2 py-1">SSID</th>
                <th className="px-2 py-1">crypto</th>
                <th className="px-2 py-1">device</th>
                <th className="px-2 py-1">network</th>
                <th className="px-2 py-1">enabled</th>
                <th className="px-2 py-1 text-right">action</th>
              </tr>
            </thead>
            <tbody>
              {orphans.map((o) => (
                <OrphanRow
                  key={o.section}
                  o={o}
                  checked={selected.has(o.section)}
                  onToggle={() => toggleOne(o.section)}
                  onDelete={() => {
                    if (confirm(`Supprimer la section ${o.section} ?`)) {
                      delOne.mutate(o.section);
                    }
                  }}
                  deleting={delOne.isPending}
                />
              ))}
            </tbody>
          </table>
        )}
      </section>

      {delOne.error && (
        <p className="text-[11px] text-amber-300">
          ⚠ {errorMessage(delOne.error)}
        </p>
      )}

      {lastBulk && (
        <section className="cyber-card p-3">
          <header className="cyber-label text-[10px] mb-2">
            résultat du bulk delete
          </header>
          <ul className="text-[11px] font-mono space-y-0.5">
            {Object.entries(lastBulk).map(([sec, res]) => (
              <li
                key={sec}
                className={cn(
                  "flex items-center gap-2",
                  res === "deleted"
                    ? "text-emerald-300"
                    : res.startsWith("skipped")
                      ? "text-[color:var(--color-cyber-muted)]"
                      : "text-amber-300",
                )}
              >
                {res === "deleted" ? (
                  <CheckCircle2 className="h-3 w-3" />
                ) : (
                  <AlertTriangle className="h-3 w-3" />
                )}
                <span>{sec}</span>
                <span>·</span>
                <span>{res}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function OrphanRow({
  o,
  checked,
  onToggle,
  onDelete,
  deleting,
}: {
  o: WifiOrphan;
  checked: boolean;
  onToggle: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <tr className="border-t border-[color:var(--color-cyber-border)]/30">
      <td className="px-2 py-1">
        <input type="checkbox" checked={checked} onChange={onToggle} />
      </td>
      <td className="px-2 py-1 text-[color:var(--color-cyber-fg)]">
        {o.section}
      </td>
      <td className="px-2 py-1 text-[color:var(--color-cyber-muted)]">
        {o.type}
      </td>
      <td className="px-2 py-1">{o.ssid || "—"}</td>
      <td className="px-2 py-1 text-[10px]">{o.encryption || "—"}</td>
      <td className="px-2 py-1 text-[10px]">{o.device || "—"}</td>
      <td className="px-2 py-1 text-[10px]">{o.network || "—"}</td>
      <td className="px-2 py-1 text-[10px]">
        {o.disabled ? (
          <span className="text-[color:var(--color-cyber-muted)]">non</span>
        ) : (
          <span className="text-emerald-300">oui</span>
        )}
      </td>
      <td className="px-2 py-1 text-right">
        <button
          onClick={onDelete}
          disabled={deleting}
          className="cyber-button-ghost p-1 text-[color:var(--color-cyber-muted)] hover:text-amber-300"
          title="Supprimer définitivement"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </td>
    </tr>
  );
}
