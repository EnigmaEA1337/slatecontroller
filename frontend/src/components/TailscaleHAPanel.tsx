/**
 * HA watchdog control panel for the Tailscale exit-node.
 *
 * Behaviour-side: the watchdog lives in the backend and re-reads its
 * config from the DB each tick. So this panel only does CRUD on config
 * + displays the latest tick state. No live SSH is triggered from here.
 *
 * Ordering of candidates: drag-and-drop is overkill for ~5 entries.
 * Two up/down arrows + delete per row + an "add" input at the bottom.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Plus,
  RefreshCw,
  ShieldOff,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import {
  getTailscaleHA,
  getTailscaleStatus,
  updateTailscaleHA,
} from "@/api/tailscale";
import type {
  HAFailsafeMode,
  TailscalePeer,
} from "@/types/tailscale";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


const ACTION_STYLE: Record<string, { chip: string; label: string }> = {
  set:             { chip: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300", label: "switched" },
  noop:            { chip: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]", label: "noop" },
  down:            { chip: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200", label: "all offline (kept)" },
  killswitch_open: { chip: "border-orange-500/60 bg-orange-500/10 text-orange-300", label: "killswitch → WAN" },
  error:           { chip: "border-red-500/60 bg-red-500/10 text-red-300", label: "error" },
};

function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const dt = new Date(iso);
  const diff = (Date.now() - dt.getTime()) / 1000;
  if (diff < 60) return `il y a ${Math.round(diff)}s`;
  if (diff < 3600) return `il y a ${Math.round(diff / 60)}min`;
  if (diff < 86400) return `il y a ${Math.round(diff / 3600)}h`;
  return dt.toLocaleString("fr-FR");
}

export default function TailscaleHAPanel() {
  const qc = useQueryClient();
  const haQ = useQuery({
    queryKey: ["tailscale", "ha"],
    queryFn: getTailscaleHA,
    // Refresh every 10s — cheap (no SSH on this endpoint, just DB read)
    // and lets the user see watchdog ticks land in near real-time.
    refetchInterval: 10_000,
  });
  // Live status used to populate the "Add candidate" dropdown with peers
  // that actually advertise exit-node capability.
  const statusQ = useQuery({
    queryKey: ["tailscale", "status"],
    queryFn: getTailscaleStatus,
    refetchInterval: 30_000,
  });

  // Local draft so the user can re-order before saving.
  const [draft, setDraft] = useState<string[] | null>(null);
  const [interval_, setInterval_] = useState<number | null>(null);
  const [failsafe, setFailsafe] = useState<HAFailsafeMode | null>(null);
  const [newCandidate, setNewCandidate] = useState("");
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (haQ.data && draft === null) {
      setDraft([...haQ.data.candidates]);
      setInterval_(haQ.data.check_interval_seconds);
      setFailsafe(haQ.data.failsafe_mode);
    }
  }, [haQ.data, draft]);

  const mutation = useMutation({
    mutationFn: updateTailscaleHA,
    onSuccess: (data) => {
      qc.setQueryData(["tailscale", "ha"], data);
      setDraft([...data.candidates]);
      setInterval_(data.check_interval_seconds);
      setFailsafe(data.failsafe_mode);
    },
  });

  if (!haQ.data) {
    return (
      <div className="cyber-panel p-5 text-[11px] text-[color:var(--color-cyber-muted)]">
        Chargement HA…
      </div>
    );
  }

  const state = haQ.data;
  const eligiblePeers: TailscalePeer[] = (statusQ.data?.peers || []).filter(
    (p) => p.exit_node_option,
  );
  const candidates = draft ?? state.candidates;
  const isDirty =
    JSON.stringify(candidates) !== JSON.stringify(state.candidates) ||
    interval_ !== state.check_interval_seconds ||
    failsafe !== state.failsafe_mode;

  function move(idx: number, delta: number) {
    const next = [...candidates];
    const j = idx + delta;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    setDraft(next);
  }
  function remove(idx: number) {
    const next = [...candidates];
    next.splice(idx, 1);
    setDraft(next);
  }
  function add(value: string) {
    const v = value.trim();
    if (!v || candidates.includes(v)) return;
    setDraft([...candidates, v]);
    setNewCandidate("");
  }

  const actionStyle = state.last_action
    ? ACTION_STYLE[state.last_action] ?? ACTION_STYLE.noop
    : null;

  return (
    <div className="cyber-panel space-y-4 p-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 text-left"
      >
        <Zap className="cyber-glow h-5 w-5" />
        <div className="flex-1">
          <h2 className="cyber-display cyber-glow text-base">HA exit-node watchdog</h2>
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            auto-failover entre peers exit-node + killswitch en cas de tous offline
          </div>
        </div>
        {state.enabled && actionStyle && (
          <span
            className={cn(
              "border px-2 py-[1px] text-[10px] font-bold uppercase tracking-[0.18em]",
              actionStyle.chip,
            )}
          >
            {actionStyle.label}
          </span>
        )}
        {open ? (
          <ChevronDown className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        ) : (
          <ChevronRight className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        )}
      </button>

      {open && (
        <>
          {/* Enable toggle */}
          <label className="flex cursor-pointer items-start gap-2 border border-[color:var(--color-cyber-border)] p-3">
            <input
              type="checkbox"
              checked={state.enabled}
              onChange={(e) => mutation.mutate({ enabled: e.target.checked })}
              className="mt-1 accent-[color:var(--color-cyber-accent)]"
            />
            <div className="text-[11px]">
              <div className="cyber-label">Watchdog activé</div>
              <div className="mt-0.5 text-[10px] text-[color:var(--color-cyber-muted)]">
                Vérifie l'exit-node configuré toutes les {state.check_interval_seconds}s
                et bascule sur le premier peer online de la liste.
              </div>
            </div>
          </label>

          {/* Candidates list */}
          <div className="space-y-2">
            <div className="cyber-label text-[10px]">
              Candidats exit-node (ordre = priorité)
            </div>
            {candidates.length === 0 && (
              <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
                Aucun candidat. Ajoute au moins un peer offrant exit-node.
              </div>
            )}
            {candidates.map((c, idx) => (
              <div
                key={c}
                className="flex items-center gap-2 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-1.5 text-xs"
              >
                <span className="cyber-label text-[10px]">#{idx + 1}</span>
                <span className="flex-1 font-mono">{c}</span>
                <button
                  type="button"
                  onClick={() => move(idx, -1)}
                  disabled={idx === 0}
                  className="p-1 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-30"
                  aria-label="Monter"
                >
                  <ArrowUp className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => move(idx, 1)}
                  disabled={idx === candidates.length - 1}
                  className="p-1 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-30"
                  aria-label="Descendre"
                >
                  <ArrowDown className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => remove(idx)}
                  className="p-1 text-red-300 hover:text-red-100"
                  aria-label="Supprimer"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}

            {/* Add candidate */}
            <div className="flex gap-2">
              {eligiblePeers.length > 0 ? (
                <select
                  value={newCandidate}
                  onChange={(e) => setNewCandidate(e.target.value)}
                  className="flex-1 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
                >
                  <option value="">— peer éligible (offre exit-node) —</option>
                  {eligiblePeers.map((p) => (
                    <option key={p.hostname || p.dns_name} value={p.hostname || p.dns_name}>
                      {p.hostname || p.dns_name}{" "}
                      {p.online ? "(online)" : "(offline)"}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={newCandidate}
                  onChange={(e) => setNewCandidate(e.target.value)}
                  placeholder="hostname ou 100.x.x.x"
                  className="flex-1 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
                />
              )}
              <button
                type="button"
                onClick={() => add(newCandidate)}
                disabled={!newCandidate.trim()}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/10 disabled:opacity-50"
              >
                <Plus className="h-3 w-3" />
                ajouter
              </button>
            </div>
          </div>

          {/* Interval slider */}
          <div className="space-y-1">
            <div className="cyber-label flex items-center gap-2 text-[10px]">
              <span>Intervalle de check</span>
              <span className="font-mono text-[color:var(--color-cyber-fg)]">
                {interval_}s
              </span>
            </div>
            <input
              type="range"
              min={15}
              max={600}
              step={5}
              value={interval_ ?? 60}
              onChange={(e) => setInterval_(Number(e.target.value))}
              className="w-full accent-[color:var(--color-cyber-accent)]"
            />
            <div className="flex justify-between text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
              <span>15s</span>
              <span>10min</span>
            </div>
          </div>

          {/* Failsafe radio */}
          <div className="space-y-1.5">
            <div className="cyber-label text-[10px]">
              Killswitch — si TOUS les candidats sont offline
            </div>
            <label className="flex cursor-pointer items-start gap-2 border border-[color:var(--color-cyber-border)] p-2">
              <input
                type="radio"
                name="failsafe"
                checked={failsafe === "fail_open"}
                onChange={() => setFailsafe("fail_open")}
                className="mt-1 accent-[color:var(--color-cyber-accent)]"
              />
              <div className="text-[10px]">
                <div className="cyber-label flex items-center gap-1">
                  <Zap className="h-3 w-3 text-emerald-300" />
                  fail_open — drop exit-node, retour WAN (recommandé)
                </div>
                <div className="mt-0.5 text-[9px] text-[color:var(--color-cyber-muted)]">
                  Évite "plus d'Internet car la route par défaut pointe sur un peer mort".
                  Le Slate reprend sa WAN locale. Re-bascule sur le premier candidat dès qu'il revient online.
                </div>
              </div>
            </label>
            <label className="flex cursor-pointer items-start gap-2 border border-[color:var(--color-cyber-border)] p-2">
              <input
                type="radio"
                name="failsafe"
                checked={failsafe === "keep"}
                onChange={() => setFailsafe("keep")}
                className="mt-1 accent-[color:var(--color-cyber-accent)]"
              />
              <div className="text-[10px]">
                <div className="cyber-label flex items-center gap-1">
                  <ShieldOff className="h-3 w-3 text-yellow-300" />
                  keep — préserve l'exit-node (pas de killswitch)
                </div>
                <div className="mt-0.5 text-[9px] text-[color:var(--color-cyber-muted)]">
                  Privacy strict : préfère NE PAS avoir d'Internet plutôt que sortir
                  par la WAN locale. Tu peux te retrouver sans Internet jusqu'au retour d'un peer.
                </div>
              </div>
            </label>
          </div>

          {/* Save */}
          {isDirty && (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  mutation.mutate({
                    candidates,
                    check_interval_seconds: interval_ ?? undefined,
                    failsafe_mode: failsafe ?? undefined,
                  })
                }
                disabled={mutation.isPending}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
              >
                <CheckCircle2 className="h-3 w-3" />
                {mutation.isPending ? "envoi…" : "appliquer"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDraft([...state.candidates]);
                  setInterval_(state.check_interval_seconds);
                  setFailsafe(state.failsafe_mode);
                }}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
              >
                <X className="h-3 w-3" />
                annuler
              </button>
            </div>
          )}
          {mutation.isError && (
            <div className="border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
              <AlertTriangle className="mr-1 inline h-3 w-3" />
              {errorMessage(mutation.error)}
            </div>
          )}

          {/* Live state */}
          <div className="border-t border-[color:var(--color-cyber-border)] pt-3">
            <div className="cyber-label mb-2 flex items-center gap-2 text-[10px]">
              <span>État watchdog</span>
              {haQ.isFetching && (
                <RefreshCw className="h-3 w-3 animate-spin text-[color:var(--color-cyber-muted)]" />
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 text-[11px] md:grid-cols-4">
              <Stat label="Dernière action" value={
                actionStyle ? actionStyle.label : "—"
              } />
              <Stat label="Quand" value={formatRelative(state.last_action_at)} />
              <Stat label="Exit-node courant" value={state.last_target || "(none/WAN)"} accent />
              <Stat label="Dernier switch" value={formatRelative(state.last_switched_at)} />
            </div>
            {state.last_action_detail && (
              <div className="mt-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                <span className="cyber-label">détail</span>{" "}
                <span className="font-mono">{state.last_action_detail}</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Stat({
  label, value, accent,
}: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div className="cyber-label text-[9px]">{label}</div>
      <div
        className={cn(
          "font-mono text-[11px]",
          accent
            ? "text-[color:var(--color-cyber-accent)]"
            : "text-[color:var(--color-cyber-fg)]",
        )}
      >
        {value}
      </div>
    </div>
  );
}
