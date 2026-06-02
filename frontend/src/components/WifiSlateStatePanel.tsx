/**
 * "État live côté Slate" — matrice indexée par slot, colonnes par bande.
 *
 *  Slot │  ra (2.4)   │  rai (5)    │  rax (6)    │  MLO
 *   0   │ ra0  …      │ rai0 …      │ rax0 …      │  —
 *   1   │ ra1  …      │ rai1 …      │ rax1 …      │  —
 *   2   │ ra2  …      │ rai2 …      │ rax2 …      │  mld0
 *   ...
 *
 * Le slot index vient du suffixe de l'ifname (`ra0` → 0, `rax15` →
 * 15). Chaque cellule affiche le SSID effectivement broadcastés sur
 * ce VAP, avec un dot de status :
 *   - vert  : config aligned + broadcast actif
 *   - orange: drift (config et iwinfo divergent)
 *   - gris  : pas de slot à cette position OU config off
 *
 * Hover sur une cellule = tooltip avec section UCI, network, notes.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { getSlateWifiState, WifiSlotState } from "@/api/wifi";
import { errorMessage } from "@/lib/error-utils";
import { cn } from "@/lib/utils";

type Family = "ra" | "rai" | "rax" | "mld";

interface ParsedIfname {
  family: Family | null;
  slot: number | null;
}

const FAMILIES: { key: Family; label: string }[] = [
  { key: "ra", label: "ra · 2.4 GHz" },
  { key: "rai", label: "rai · 5 GHz" },
  { key: "rax", label: "rax · 6 GHz" },
];

function parseIfname(ifname: string): ParsedIfname {
  // ra0 / ra15 → family=ra, slot=0/15
  // rai0 / rai6 → family=rai
  // rax0 / rax5 → family=rax
  // mld0 / mld1 → family=mld
  for (const fam of ["rax", "rai", "ra", "mld"] as const) {
    const re = new RegExp(`^${fam}(\\d+)$`);
    const m = ifname.match(re);
    if (m && m[1]) return { family: fam, slot: parseInt(m[1], 10) };
  }
  return { family: null, slot: null };
}

export default function WifiSlateStatePanel() {
  const q = useQuery({
    queryKey: ["wifi", "slate-state"],
    queryFn: getSlateWifiState,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  return (
    <section className="cyber-panel p-4">
      <header className="mb-3 flex items-center justify-between">
        <div className="cyber-label text-[10px]">
          radios · état live côté slate
        </div>
        <button
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          className="rounded border border-[color:var(--color-cyber-border)] px-2 py-1 text-[9px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
        >
          <RefreshCw
            className={cn("mr-1 inline h-2.5 w-2.5", q.isFetching && "animate-spin")}
          />
          Refresh
        </button>
      </header>

      {q.isError && (
        <div className="rounded border border-red-500/40 bg-red-500/5 p-2 text-[11px] text-red-300">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          {errorMessage(q.error)}
        </div>
      )}

      {q.isLoading && !q.data && (
        <div className="flex items-center gap-2 p-2 text-[11px] text-[color:var(--color-cyber-muted)]">
          <RefreshCw className="h-3 w-3 animate-spin" /> probe SSH…
        </div>
      )}

      {q.data && <SlotMatrix slots={q.data} />}
    </section>
  );
}

/* ---------- matrix ---------- */

function SlotMatrix({ slots }: { slots: WifiSlotState[] }) {
  // Index : pour chaque (family, slot) on garde le `WifiSlotState`
  // dont l'ifname correspond. Les sections sans ifname numéroté
  // (named GL.iNet stock comme `guest2g`, ou MLO links `wlanmld5g`)
  // sont rattachées via leur ifname (qui pointe vers une iface
  // numérotée du pool).
  const cells = new Map<string, WifiSlotState>(); // "ra-0" → state
  const mldByGroup = new Map<number, WifiSlotState>(); // 0 → mld0 state
  const mldSlotsCovered = new Map<number, Set<number>>(); // mld# → slots where it broadcasts

  for (const s of slots) {
    const p = parseIfname(s.ifname);
    if (p.family === "mld" && p.slot !== null) {
      mldByGroup.set(p.slot, s);
      continue;
    }
    if (p.family && p.family !== "mld" && p.slot !== null) {
      cells.set(`${p.family}-${p.slot}`, s);
    }
  }

  // Find MLO link assignments : a section whose name starts with
  // wlanmld* uses an ifname like rai2/rax2. The slot number of that
  // ifname is where the MLD group broadcasts.
  for (const s of slots) {
    if (s.slot_kind !== "mlo_link") continue;
    const p = parseIfname(s.ifname);
    if (!p.family || p.slot === null) continue;
    // Map the link section name to its MLD group : "wlanmld5g" + "wlanmld6g"
    // attach to mld0 ; "wlanmldguest5g/6g" → mld1. Naming convention from
    // the wifi.sh handler + GL.iNet stock. Fallback to mld0 when
    // ambiguous.
    const groupNum = s.section_name.includes("guest") ? 1 : 0;
    if (!mldSlotsCovered.has(groupNum)) mldSlotsCovered.set(groupNum, new Set());
    mldSlotsCovered.get(groupNum)!.add(p.slot);
  }

  // Compute the union of slot indices present anywhere.
  const slotSet = new Set<number>();
  for (const key of cells.keys()) {
    const numStr = key.split("-")[1];
    if (!numStr) continue;
    const num = parseInt(numStr, 10);
    if (!Number.isNaN(num)) slotSet.add(num);
  }
  const slotIndices = Array.from(slotSet).sort((a, b) => a - b);

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[11px]">
        <thead>
          <tr className="text-[color:var(--color-cyber-muted)]">
            <Th>slot</Th>
            {FAMILIES.map((f) => (
              <Th key={f.key}>{f.label}</Th>
            ))}
            <Th>MLO</Th>
          </tr>
        </thead>
        <tbody>
          {slotIndices.map((idx) => (
            <tr
              key={idx}
              className="border-t border-[color:var(--color-cyber-border)]/30"
            >
              <Td mono accent>
                {idx}
              </Td>
              {FAMILIES.map((f) => (
                <Td key={f.key}>
                  <Cell state={cells.get(`${f.key}-${idx}`)} />
                </Td>
              ))}
              <Td>
                <MloCell
                  groups={[...mldByGroup.entries()].filter(([num]) =>
                    mldSlotsCovered.get(num)?.has(idx),
                  )}
                />
              </Td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* MLD groups dump under the matrix — context for the MLO column. */}
      {mldByGroup.size > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-[color:var(--color-cyber-muted)]">
          {[...mldByGroup.entries()].map(([num, state]) => (
            <span
              key={num}
              className="rounded border border-cyan-500/30 bg-cyan-500/5 px-1.5 py-0.5"
            >
              mld{num} → <span className="font-mono">{state.ssid_uci || "—"}</span>
              {state.enabled ? " · enabled" : " · disabled"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- cells ---------- */

function Cell({ state }: { state: WifiSlotState | undefined }) {
  if (!state) {
    return <span className="text-[color:var(--color-cyber-muted)]">—</span>;
  }
  const drift = state.notes.length > 0;
  const status: "ok" | "drift" | "off" = drift
    ? "drift"
    : state.is_up
      ? "ok"
      : "off";
  const dot = {
    ok: "bg-emerald-400",
    drift: "bg-amber-400",
    off: "bg-[color:var(--color-cyber-dim)]",
  }[status];
  const text = {
    ok: "text-emerald-200",
    drift: "text-amber-200",
    off: "text-[color:var(--color-cyber-muted)]",
  }[status];
  const ssid = state.ssid_broadcast || state.ssid_uci || "—";
  const tooltip = [
    `UCI : ${state.section_name}`,
    `iface : ${state.ifname}`,
    `Config : ${state.enabled ? "enabled" : "disabled"} · réseau=${state.network || "—"}`,
    `Broadcast : ${state.ssid_broadcast ? `"${state.ssid_broadcast}"` : "silent"}`,
    state.notes.length > 0 ? `⚠ ${state.notes.join(" · ")}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span
      className={cn("inline-flex items-center gap-1.5 font-mono", text)}
      title={tooltip}
    >
      <span className={cn("inline-block h-1.5 w-1.5 rounded-full", dot)} />
      <span className="truncate">{ssid}</span>
      {drift && <AlertTriangle className="h-2.5 w-2.5 shrink-0 text-amber-400" />}
    </span>
  );
}

function MloCell({ groups }: { groups: [number, WifiSlotState][] }) {
  if (groups.length === 0) {
    return <span className="text-[color:var(--color-cyber-muted)]">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {groups.map(([num, state]) => {
        const drift = !state.enabled && state.is_up;
        const color = state.enabled
          ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-200"
          : drift
            ? "border-amber-500/50 bg-amber-500/10 text-amber-200"
            : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]";
        return (
          <span
            key={num}
            className={cn(
              "rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.15em]",
              color,
            )}
            title={`mld${num} : ${state.ssid_uci || "(vide)"} · ${state.enabled ? "enabled" : "disabled"}`}
          >
            mld{num}
          </span>
        );
      })}
    </div>
  );
}

/* ---------- primitives ---------- */

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="border-b border-[color:var(--color-cyber-border)] px-2 py-1 text-left font-mono text-[10px] uppercase tracking-[0.15em]">
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
  accent,
}: {
  children: React.ReactNode;
  mono?: boolean;
  accent?: boolean;
}) {
  return (
    <td
      className={cn(
        "px-2 py-1.5 text-[color:var(--color-cyber-dim)]",
        mono && "font-mono",
        accent && "text-[color:var(--color-cyber-accent)] cyber-glow",
      )}
    >
      {children}
    </td>
  );
}
