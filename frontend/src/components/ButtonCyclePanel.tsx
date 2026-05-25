/**
 * Settings panel for the Slate's reset-button profile cycle.
 *
 * UX :
 *   - Two columns. Left = available profiles + action library. Right =
 *     the ordered cycle list ("Press the reset button for less than 3s
 *     to advance one slot").
 *   - Click an item on the left to append it to the cycle.
 *   - Per-slot ▲ ▼ buttons reorder, ✕ removes.
 *   - Save pushes to /api/settings/button-cycle which also writes
 *     /etc/slate-controller/cycle.json on the Slate in the same call.
 *
 * The cycle is stored on the Slate and runs autonomously — no
 * controller round-trip on button press. This panel just edits the list
 * + triggers a one-shot sync.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  CircleDot,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  Zap,
} from "lucide-react";

import {
  type CycleStep,
  getButtonCycle,
  saveButtonCycle,
} from "@/api/button-cycle";
import { listProfiles } from "@/api/profiles";
import { errorMessage } from "@/lib/error-utils";

// Action library — extend by adding more cycle-action-*.sh handlers on
// the agent side. The `name` here must match the suffix of the handler
// file (cycle-action-<name>.sh).
const ACTION_LIBRARY: Array<{ name: string; label: string; help: string }> = [
  {
    name: "update",
    label: "Update from controller",
    help:
      "V1 : drops a marker file on the Slate and shows a screen toast. "
      + "Future revisions will pull fresh config from the controller.",
  },
];

function stepsEqual(a: CycleStep[], b: CycleStep[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    // Bounds-checked by the length guard above ; both sides are CycleStep.
    const ai = a[i] as CycleStep;
    const bi = b[i] as CycleStep;
    if (ai.kind !== bi.kind || ai.name !== bi.name) return false;
  }
  return true;
}

export default function ButtonCyclePanel() {
  const qc = useQueryClient();
  const cycleQ = useQuery({
    queryKey: ["settings", "button-cycle"],
    queryFn: getButtonCycle,
  });
  const profilesQ = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
    staleTime: 30_000,
  });

  // Working copy — separate from the server snapshot so we can show the
  // user pending edits + a Save button. Synced from server on every load.
  const [draft, setDraft] = useState<CycleStep[]>([]);
  const [lastSaveMsg, setLastSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    if (cycleQ.data) setDraft(cycleQ.data.steps);
  }, [cycleQ.data]);

  const serverSteps = cycleQ.data?.steps ?? [];
  const dirty = !stepsEqual(draft, serverSteps);

  const profileNames = useMemo(
    () => (profilesQ.data ?? []).map((p) => p.profile.name),
    [profilesQ.data],
  );

  const save = useMutation({
    mutationFn: () => saveButtonCycle(draft),
    onSuccess: (data) => {
      setDraft(data.steps);
      qc.setQueryData(["settings", "button-cycle"], { steps: data.steps });
      setLastSaveMsg(
        data.pushed_to_slate
          ? "Sauvegardé + poussé sur le Slate"
          : `Sauvegardé (push Slate : ${data.push_error ?? "échec"})`,
      );
    },
  });

  function appendProfile(name: string) {
    setDraft((s) => [...s, { kind: "profile", name }]);
  }
  function appendAction(name: string) {
    setDraft((s) => [...s, { kind: "action", name }]);
  }
  function moveUp(i: number) {
    setDraft((s) => {
      if (i <= 0) return s;
      const next = [...s];
      // i and i-1 are in bounds (checked above) ; non-null asserted
      // to satisfy strict mode against the noUncheckedIndexedAccess rule.
      const tmp = next[i - 1] as CycleStep;
      next[i - 1] = next[i] as CycleStep;
      next[i] = tmp;
      return next;
    });
  }
  function moveDown(i: number) {
    setDraft((s) => {
      if (i >= s.length - 1) return s;
      const next = [...s];
      const tmp = next[i] as CycleStep;
      next[i] = next[i + 1] as CycleStep;
      next[i + 1] = tmp;
      return next;
    });
  }
  function removeAt(i: number) {
    setDraft((s) => s.filter((_, idx) => idx !== i));
  }
  function reset() {
    setDraft(serverSteps);
    setLastSaveMsg(null);
  }
  function clear() {
    setDraft([]);
  }

  return (
    <section className="cyber-card p-6">
      <div className="mb-2 flex items-center gap-2">
        <CircleDot className="cyber-glow h-4 w-4" />
        <h2 className="cyber-display cyber-glow text-base">
          Bouton reset · cycle profils
        </h2>
      </div>
      <p className="mb-4 text-[11px] leading-relaxed text-[color:var(--color-cyber-muted)]">
        Court appui (&lt; 3 s) sur le reset du Slate → avance d'un slot
        dans la liste ci-dessous. Le cycle tourne 100 % en local sur le
        Slate (aucun appel au contrôleur). Comportements OEM préservés :
        3-7 s = reset réseau, 8 s+ = factory reset.
      </p>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ── Library (left) ───────────────────────────────────── */}
        <div className="space-y-3">
          <div className="cyber-label text-[9px]">profils disponibles</div>
          <div className="space-y-1">
            {profilesQ.isLoading && (
              <p className="text-[10px] text-[color:var(--color-cyber-muted)]">
                chargement…
              </p>
            )}
            {profileNames.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => appendProfile(name)}
                className="flex w-full items-center justify-between border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40 px-3 py-1.5 text-left text-[11px] hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/8"
              >
                <span className="font-mono">{name}</span>
                <span className="text-[9px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
                  + add
                </span>
              </button>
            ))}
          </div>

          <div className="cyber-label mt-4 text-[9px]">actions spéciales</div>
          <div className="space-y-1">
            {ACTION_LIBRARY.map((act) => (
              <button
                key={act.name}
                type="button"
                onClick={() => appendAction(act.name)}
                title={act.help}
                className="flex w-full items-center justify-between border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40 px-3 py-1.5 text-left text-[11px] hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/8"
              >
                <span className="flex items-center gap-1.5">
                  <Zap className="h-3 w-3 text-yellow-400" />
                  <span>{act.label}</span>
                </span>
                <span className="text-[9px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
                  + add
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* ── Cycle (right) ────────────────────────────────────── */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="cyber-label text-[9px]">
              ordre du cycle · {draft.length} slot
              {draft.length !== 1 ? "s" : ""}
            </div>
            {draft.length > 0 && (
              <button
                type="button"
                onClick={clear}
                className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
              >
                vider
              </button>
            )}
          </div>

          {draft.length === 0 ? (
            <p className="border border-dashed border-[color:var(--color-cyber-border)] px-3 py-6 text-center text-[11px] text-[color:var(--color-cyber-muted)]">
              Cycle vide — le bouton reset short-press est inactif.
              <br />
              Clique sur un profil à gauche pour l'ajouter.
            </p>
          ) : (
            draft.map((step, i) => (
              <div
                key={`${i}-${step.kind}-${step.name}`}
                className="flex items-center gap-2 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-bg-2)]/60 px-3 py-2 text-[11px]"
              >
                <span className="cyber-label !text-[9px] text-[color:var(--color-cyber-accent)]">
                  #{i + 1}
                </span>
                {step.kind === "action" ? (
                  <Zap className="h-3 w-3 text-yellow-400" />
                ) : (
                  <CircleDot className="h-3 w-3 text-[color:var(--color-cyber-accent)]" />
                )}
                <span className="flex-1 font-mono">
                  {step.kind === "action" ? "@" : ""}
                  {step.name}
                </span>
                <button
                  type="button"
                  onClick={() => moveUp(i)}
                  disabled={i === 0}
                  className="px-1 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-30"
                  aria-label="Monter"
                >
                  <ArrowUp className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => moveDown(i)}
                  disabled={i === draft.length - 1}
                  className="px-1 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-30"
                  aria-label="Descendre"
                >
                  <ArrowDown className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => removeAt(i)}
                  className="px-1 text-[color:var(--color-cyber-muted)] hover:text-red-300"
                  aria-label="Retirer"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── Save row ─────────────────────────────────────────── */}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-[color:var(--color-cyber-border)] pt-4">
        <button
          type="button"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate()}
          className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-40"
        >
          <Save className="h-3.5 w-3.5" />
          {save.isPending ? "envoi…" : "Enregistrer + push Slate"}
        </button>

        <button
          type="button"
          onClick={reset}
          disabled={!dirty || save.isPending}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-40"
        >
          <RotateCcw className="h-3 w-3" />
          annuler
        </button>

        <button
          type="button"
          onClick={() => cycleQ.refetch()}
          className="ml-auto inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          <RefreshCw className="h-3 w-3" />
          rafraîchir
        </button>
      </div>

      {save.isError && (
        <p className="mt-3 flex items-start gap-2 text-[11px] text-red-300">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          {errorMessage(save.error)}
        </p>
      )}
      {save.isSuccess && lastSaveMsg && (
        <p className="mt-3 flex items-start gap-2 text-[11px] text-emerald-300">
          <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" />
          {lastSaveMsg}
        </p>
      )}
    </section>
  );
}
