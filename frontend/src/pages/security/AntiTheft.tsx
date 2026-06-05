// Anti-theft autonomous mode + auto-erase configuration page.

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Cog,
  Lock,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldOff,
  Smartphone,
  Trash2,
} from "lucide-react";

import {
  type AntiTheftAction,
  ACTION_META,
  getAntiTheftConfig,
  resetAntiTheftCounter,
  testAntiTheftAction,
  updateAntiTheftConfig,
} from "@/api/anti-theft";
import { cn } from "@/lib/utils";

export default function AntiTheftPage() {
  const qc = useQueryClient();
  const cfg = useQuery({
    queryKey: ["security", "anti-theft"],
    queryFn: () => getAntiTheftConfig(),
    refetchInterval: 10_000,
  });

  const [autonomous, setAutonomous] = useState<boolean | null>(null);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [action, setAction] = useState<AntiTheftAction | null>(null);
  const [webhook, setWebhook] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  // Sync local state on first load (and on remote refetch when not yet
  // touched locally). useMemo for the dependency hack.
  useMemo(() => {
    if (!cfg.data) return;
    if (autonomous === null) setAutonomous(cfg.data.autonomous_mode);
    if (threshold === null) setThreshold(cfg.data.failure_threshold);
    if (action === null) setAction(cfg.data.action);
    if (webhook === null) setWebhook(cfg.data.notify_webhook_url);
  }, [cfg.data, autonomous, threshold, action, webhook]);

  const saveMut = useMutation({
    mutationFn: () =>
      updateAntiTheftConfig({
        autonomous_mode: autonomous ?? false,
        failure_threshold: threshold ?? 10,
        action: action ?? "alert",
        notify_webhook_url: webhook ?? "",
      }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["security", "anti-theft"] }),
  });
  const resetMut = useMutation({
    mutationFn: () => resetAntiTheftCounter(),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["security", "anti-theft"] }),
  });
  const testMut = useMutation({
    mutationFn: () => testAntiTheftAction(),
    onSuccess: (r) => setTestResult(r.summary),
  });

  if (!cfg.data) {
    return (
      <div className="cyber-card p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Chargement de la politique anti-theft…
      </div>
    );
  }

  const c = cfg.data;
  const effectiveAutonomous = autonomous ?? c.autonomous_mode;
  const effectiveThreshold = threshold ?? c.failure_threshold;
  const effectiveAction = action ?? c.action;

  const dirty =
    autonomous !== c.autonomous_mode ||
    threshold !== c.failure_threshold ||
    action !== c.action ||
    (webhook ?? "") !== c.notify_webhook_url;

  const tripPercent = Math.min(
    100,
    Math.round((c.total_failures / Math.max(1, effectiveThreshold)) * 100),
  );

  return (
    <div className="space-y-4">
      <header className="cyber-label flex items-center gap-2">
        <ShieldAlert className="h-3 w-3" /> anti-theft · mode autonome
      </header>

      <p className="text-xs text-[color:var(--color-cyber-muted)] max-w-2xl">
        Quand le mode autonome est activ&eacute;, un d&eacute;passement du seuil de PIN
        rat&eacute;s d&eacute;clenche l'action choisie automatiquement. La protection 3
        tentatives / 60s reste active dans tous les cas — l'autonome ajoute
        une escalade au-dessus.
      </p>

      <section className="cyber-card p-4 space-y-3">
        <header className="flex items-center justify-between">
          <div className="cyber-label text-[10px] flex items-center gap-2">
            <Cog className="h-3 w-3" /> politique
          </div>
          <StatusChip
            autonomous={c.autonomous_mode}
            action={c.action}
          />
        </header>

        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={effectiveAutonomous}
            onChange={(e) => setAutonomous(e.target.checked)}
            className="cyber-checkbox"
          />
          <span>Activer le mode autonome</span>
        </label>

        <div
          className={cn(
            !effectiveAutonomous && "opacity-50 pointer-events-none",
            "space-y-3",
          )}
        >
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              seuil d'échecs cumulés&nbsp;
              <span className="text-[color:var(--color-cyber-accent)] font-mono">
                {effectiveThreshold}
              </span>
            </label>
            <input
              type="range"
              min={3}
              max={50}
              value={effectiveThreshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-full"
            />
            <div className="text-[10px] text-[color:var(--color-cyber-muted)] font-mono mt-1">
              Compteur cumul&eacute; — ne se reset qu'apr&egrave;s un PIN correct
              (jamais sur le lockout 60s).
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              action à déclencher
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {(["alert", "soft_wipe"] as AntiTheftAction[]).map((opt) => (
                <button
                  key={opt}
                  onClick={() => setAction(opt)}
                  type="button"
                  className={cn(
                    "flex flex-col items-start gap-0.5 px-3 py-2 border rounded-sm text-left text-xs",
                    effectiveAction === opt
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
                      : "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-border-strong)]",
                  )}
                >
                  <span
                    className="font-mono"
                    style={{ color: ACTION_META[opt].color }}
                  >
                    {opt === "alert" ? "ⓘ" : "⚠"} {ACTION_META[opt].label}
                  </span>
                  <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
                    {ACTION_META[opt].hint}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              webhook de notification (optionnel)
            </label>
            <input
              type="text"
              value={webhook ?? ""}
              onChange={(e) => setWebhook(e.target.value)}
              placeholder="https://example.com/hook"
              className="cyber-input w-full text-xs font-mono"
            />
            <div className="text-[10px] text-[color:var(--color-cyber-muted)] mt-1">
              Appel&eacute; avant l'action. Pas encore impl&eacute;ment&eacute; — reserved
              pour la phase Webhooks Slate → Controller.
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-[color:var(--color-cyber-border)]/40">
          <button
            onClick={() => saveMut.mutate()}
            disabled={!dirty || saveMut.isPending}
            className="cyber-button px-3 py-1.5 text-xs flex-1"
          >
            {saveMut.isPending ? "…" : dirty ? "enregistrer" : "à jour"}
          </button>
          <button
            onClick={() => testMut.mutate()}
            disabled={testMut.isPending}
            className="cyber-button-ghost px-3 py-1.5 text-xs"
            title="Dry-run : aucune donnée touchée, montre ce qui se passerait"
          >
            <PlayCircle className="h-3 w-3 inline mr-1" />
            tester
          </button>
        </div>

        {testResult && (
          <div
            className="text-[11px] p-2 rounded-sm border font-mono"
            style={{
              borderColor: "var(--color-cyber-accent-dim)",
              background: "var(--color-cyber-bg-2)",
            }}
          >
            {testResult}
          </div>
        )}
      </section>

      <LockoutPanel lockout={c.lockout} />
      <TouchscreenPanel touch={c.touchscreen} />

      <section className="cyber-card p-4">
        <header className="cyber-label text-[10px] mb-2">compteur live</header>
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs">
            <span className="font-mono text-[color:var(--color-cyber-accent)]">
              {c.total_failures}
            </span>{" "}
            <span className="text-[color:var(--color-cyber-muted)]">
              / {effectiveThreshold} échecs cumulés
            </span>
          </div>
          <button
            onClick={() => resetMut.mutate()}
            disabled={resetMut.isPending || c.total_failures === 0}
            className="cyber-button-ghost px-2 py-1 text-[10px] flex items-center gap-1 disabled:opacity-40"
            title="Reset manuel — après une r&eacute;cup&eacute;ration l&eacute;gitime"
          >
            <RotateCcw className="h-3 w-3" /> reset
          </button>
        </div>
        <div className="h-2 w-full bg-[color:var(--color-cyber-bg-2)] rounded-sm overflow-hidden">
          <div
            className="h-full transition-all"
            style={{
              width: `${tripPercent}%`,
              background:
                tripPercent >= 80
                  ? "#fbbf24"
                  : tripPercent >= 50
                    ? "var(--color-cyber-accent)"
                    : "var(--color-cyber-accent-dim)",
            }}
          />
        </div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)] mt-1">
          Prochain &eacute;chec &agrave; {c.total_failures + 1} / {effectiveThreshold}
          {c.failures_until_trigger === 0 && c.autonomous_mode && (
            <span className="text-amber-300 ml-2">
              ⚠ prochaine erreur d&eacute;clenche l'action
            </span>
          )}
        </div>
      </section>

      {c.last_action_at && (
        <section
          className="cyber-card p-4"
          style={{ borderColor: "var(--color-cyber-accent)" }}
        >
          <header className="cyber-label text-[10px] mb-2 flex items-center gap-2 text-amber-300">
            <AlertTriangle className="h-3 w-3" /> dernière action déclenchée
          </header>
          <div className="text-xs font-mono">
            {new Date(c.last_action_at).toLocaleString("fr-FR")} ·{" "}
            <span style={{ color: ACTION_META[c.last_action_kind as AntiTheftAction]?.color }}>
              {c.last_action_kind}
            </span>
          </div>
          {c.last_action_note && (
            <div className="text-[11px] text-[color:var(--color-cyber-muted)] mt-1 font-mono break-all">
              {c.last_action_note}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function LockoutPanel({
  lockout,
}: {
  lockout: {
    failed_count: number;
    locked_until: string | null;
    remaining_attempts: number;
    remaining_lock_s: number;
  };
}) {
  // Local countdown : sync from server snapshot, then tick locally.
  // refetchInterval on the parent reconciles any drift.
  const [localRemaining, setLocalRemaining] = useState(
    lockout.remaining_lock_s,
  );
  useEffect(() => {
    setLocalRemaining(lockout.remaining_lock_s);
  }, [lockout.remaining_lock_s]);
  useEffect(() => {
    if (localRemaining <= 0) return;
    const id = window.setInterval(() => {
      setLocalRemaining((s) => Math.max(0, s - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [localRemaining > 0]);

  const locked = localRemaining > 0;
  return (
    <section
      className="cyber-card p-4"
      style={
        locked
          ? {
              borderColor: "#fbbf24",
              background:
                "linear-gradient(135deg, rgba(251, 191, 36, 0.06), transparent)",
            }
          : undefined
      }
    >
      <header className="cyber-label text-[10px] mb-2 flex items-center gap-2">
        <Lock className={cn("h-3 w-3", locked && "text-amber-300")} />
        état du verifier (lockout 60s)
      </header>
      {locked ? (
        <div className="space-y-1">
          <div className="font-mono text-xs text-amber-300">
            🔒 Verrouillé · {localRemaining}s restantes
          </div>
          <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
            Pas de nouvelle tentative tolérée jusqu'à expiration. Compteur
            d'échecs actuel : {lockout.failed_count}.
          </div>
        </div>
      ) : (
        <div className="text-xs">
          <span className="text-[color:var(--color-cyber-fg)]">
            Disponible
          </span>{" "}
          <span className="text-[color:var(--color-cyber-muted)]">
            · {lockout.remaining_attempts} tentative
            {lockout.remaining_attempts > 1 ? "s" : ""} avant lockout
            {lockout.failed_count > 0 && (
              <> · {lockout.failed_count} échec
              {lockout.failed_count > 1 ? "s" : ""} dans la fenêtre
              </>
            )}
          </span>
        </div>
      )}
    </section>
  );
}

function TouchscreenPanel({
  touch,
}: {
  touch: {
    continuous_errors: number;
    exceed_count: number;
    exceed_limit: boolean;
    last_polled_at: string | null;
    last_error: string;
  };
}) {
  const lastPoll = touch.last_polled_at
    ? new Date(touch.last_polled_at)
    : null;
  const polledMinAgo = lastPoll
    ? Math.floor((Date.now() - lastPoll.getTime()) / 60_000)
    : null;
  const stale = polledMinAgo !== null && polledMinAgo > 2;
  return (
    <section
      className="cyber-card p-4"
      style={
        touch.exceed_limit
          ? {
              borderColor: "#dc2626",
              background:
                "linear-gradient(135deg, rgba(220, 38, 38, 0.08), transparent)",
            }
          : undefined
      }
    >
      <header className="cyber-label text-[10px] mb-2 flex items-center gap-2">
        <Smartphone
          className={cn(
            "h-3 w-3",
            touch.exceed_limit && "text-red-400",
          )}
        />
        état du touchscreen (gl_screen natif)
      </header>
      {touch.exceed_limit ? (
        <div className="space-y-1">
          <div className="font-mono text-xs text-red-400">
            🔒 Touchscreen verrouillé · {touch.exceed_count} échec
            {touch.exceed_count > 1 ? "s" : ""} ont déclenché le lockout
          </div>
          <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
            Le binaire gl_screen a bloqué le clavier tactile (typiquement
            5 min). Les échecs ont déjà été cumulés dans le compteur
            anti-theft ci-dessous.
          </div>
        </div>
      ) : touch.continuous_errors > 0 ? (
        <div className="text-xs">
          <span className="text-amber-300 font-mono">
            ⚠ {touch.continuous_errors} échec
            {touch.continuous_errors > 1 ? "s" : ""}
          </span>{" "}
          <span className="text-[color:var(--color-cyber-muted)]">
            détecté{touch.continuous_errors > 1 ? "s" : ""} sur le
            touchscreen depuis le dernier reset gl_screen
          </span>
        </div>
      ) : (
        <div className="text-xs">
          <span className="text-[color:var(--color-cyber-fg)]">
            Disponible
          </span>{" "}
          <span className="text-[color:var(--color-cyber-muted)]">
            · aucun échec récent sur le touchscreen
          </span>
        </div>
      )}
      <div className="text-[9px] text-[color:var(--color-cyber-muted)] mt-2 font-mono">
        polled{" "}
        {polledMinAgo === null
          ? "jamais"
          : polledMinAgo === 0
            ? "il y a < 1 min"
            : `il y a ${polledMinAgo} min`}
        {stale && (
          <span className="text-amber-300 ml-2">⚠ snapshot ancien</span>
        )}
        {touch.last_error && (
          <span className="text-amber-300 ml-2">err: {touch.last_error}</span>
        )}
      </div>
    </section>
  );
}

function StatusChip({
  autonomous,
  action,
}: {
  autonomous: boolean;
  action: string;
}) {
  if (!autonomous) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] cyber-chip text-[color:var(--color-cyber-muted)]">
        <ShieldOff className="h-3 w-3" /> DÉSACTIVÉ
      </span>
    );
  }
  if (action === "soft_wipe") {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-amber-300">
        <Trash2 className="h-3 w-3" /> ARMÉ · soft_wipe
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-cyan-300">
      <RefreshCw className="h-3 w-3" /> ARMÉ · alert
    </span>
  );
}
