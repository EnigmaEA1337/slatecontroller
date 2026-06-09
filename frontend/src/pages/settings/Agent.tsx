/**
 * Settings → Agent — manage the on-Slate slate-ctrl agent.
 *
 * The agent is a tiny set of shell scripts that, once deployed, lets the
 * Slate apply profiles by itself — even when this controller is offline
 * or unreachable. This page is the operator's interface to:
 *
 *   1. See if it's installed (version, profiles present locally, active)
 *   2. Deploy / re-deploy the dispatcher + handlers
 *   3. Sync profile JSONs (controller → Slate)
 *   4. Trigger a local `slate-ctrl apply <name>` to test
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Cpu,
  Download,
  PlayCircle,
  RefreshCw,
  Terminal,
  Upload,
} from "lucide-react";
import {
  applyAgentProfile,
  deployAgent,
  deployAgentWebhook,
  getAgentStatus,
  rotateAgentWebhookSecret,
  syncAgentProfiles,
  type AgentApplyResult,
} from "@/api/agent";
import ButtonCyclePanel from "@/components/ButtonCyclePanel";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


export default function Agent() {
  const t = useT();
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["agent", "status"],
    queryFn: getAgentStatus,
    refetchInterval: 10_000,
  });

  const deploy = useMutation({
    mutationFn: deployAgent,
    onSettled: () => qc.invalidateQueries({ queryKey: ["agent", "status"] }),
  });

  const sync = useMutation({
    mutationFn: syncAgentProfiles,
    onSettled: () => qc.invalidateQueries({ queryKey: ["agent", "status"] }),
  });

  const [applyTarget, setApplyTarget] = useState<string | null>(null);
  const [lastApply, setLastApply] = useState<AgentApplyResult | null>(null);
  const apply = useMutation({
    mutationFn: (name: string) => applyAgentProfile(name),
    onSuccess: (data) => setLastApply(data),
    onSettled: () => qc.invalidateQueries({ queryKey: ["agent", "status"] }),
  });

  const installed = status.data?.installed === true;

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Cpu className="cyber-glow h-3 w-3" />
          {t("set_agent.subtitle")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("set_agent.title").toUpperCase()}
        >
          {t("set_agent.title").toUpperCase()}
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("set_agent.description")}
        </p>
      </header>

      {/* ── State card ────────────────────────────────────────────── */}
      <section className="cyber-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <Terminal className="cyber-glow h-4 w-4" />
          <h2 className="cyber-display cyber-glow text-base">État du déploiement</h2>
        </div>

        {status.isLoading && (
          <p className="cyber-label cyber-cursor text-[10px]">chargement</p>
        )}

        {status.data && (
          <div className="space-y-3 text-[11px]">
            <Row
              label="Installation"
              value={
                installed ? (
                  <span className="text-emerald-300">
                    ✓ déployé · {status.data.version}
                  </span>
                ) : (
                  <span className="text-[color:var(--color-cyber-muted)]">
                    non déployé — clique sur Déployer
                  </span>
                )
              }
            />
            <Row
              label="Profils sur le Slate"
              value={
                status.data.remote_profiles.length === 0 ? (
                  <span className="text-[color:var(--color-cyber-muted)]">
                    aucun — clique sur Sync
                  </span>
                ) : (
                  <span className="font-mono">
                    {status.data.remote_profiles.join(", ")}
                  </span>
                )
              }
            />
            <Row
              label="Profil actif (agent)"
              value={
                status.data.active ? (
                  <span className="font-mono text-[color:var(--color-cyber-accent)]">
                    {status.data.active}
                  </span>
                ) : (
                  <span className="text-[color:var(--color-cyber-muted)]">—</span>
                )
              }
            />
          </div>
        )}
      </section>

      {/* ── Deploy / sync actions ─────────────────────────────────── */}
      <section className="cyber-card mt-4 p-6">
        <div className="mb-4 flex items-center gap-2">
          <Download className="cyber-glow h-4 w-4" />
          <h2 className="cyber-display cyber-glow text-base">Déploiement</h2>
        </div>
        <p className="mb-4 text-[11px] text-[color:var(--color-cyber-muted)]">
          <strong>Déployer</strong> pousse <span className="font-mono">slate-ctrl</span>{" "}
          + handlers dans <span className="font-mono">/usr/local/bin/</span> et{" "}
          <span className="font-mono">/etc/slate-controller/</span> sur le Slate.
          Idempotent — relancer = mise à jour.
          <br />
          <strong>Sync</strong> envoie les JSONs de chaque profil dans{" "}
          <span className="font-mono">/etc/slate-controller/profiles/</span>.
        </p>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => deploy.mutate()}
            disabled={deploy.isPending}
            className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
          >
            <Download className="h-3 w-3" />
            {deploy.isPending ? "déploiement…" : "Déployer l'agent"}
          </button>
          <button
            type="button"
            onClick={() => sync.mutate()}
            disabled={sync.isPending || !installed}
            title={!installed ? "Déploie l'agent d'abord" : undefined}
            className={cn(
              "inline-flex items-center gap-2 border px-3 py-2 text-[10px] font-bold uppercase tracking-[0.18em]",
              "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]",
              "disabled:opacity-50",
            )}
          >
            <Upload className="h-3 w-3" />
            {sync.isPending ? "sync…" : "Sync profils"}
          </button>
        </div>

        {/* Last-op feedback */}
        <OpFeedback
          name="Déploiement"
          isError={deploy.isError}
          isSuccess={deploy.isSuccess}
          error={deploy.error}
          ok={deploy.data?.ok}
          lines={deploy.data?.pushed}
          errors={deploy.data?.errors}
        />
        <OpFeedback
          name="Sync profils JSON"
          isError={sync.isError}
          isSuccess={sync.isSuccess}
          error={sync.error}
          ok={sync.data?.profiles?.ok}
          lines={sync.data?.profiles?.pushed}
          errors={sync.data?.profiles?.errors}
        />
        <OpFeedback
          name="Sync loading screens (RGB565)"
          isError={sync.isError}
          isSuccess={sync.isSuccess}
          error={sync.error}
          ok={sync.data?.screens?.ok}
          lines={sync.data?.screens?.pushed}
          errors={sync.data?.screens?.errors}
        />
      </section>

      {/* ── Apply test ────────────────────────────────────────────── */}
      <section className="cyber-card mt-4 p-6">
        <div className="mb-4 flex items-center gap-2">
          <PlayCircle className="cyber-glow h-4 w-4" />
          <h2 className="cyber-display cyber-glow text-base">Apply local</h2>
        </div>
        <p className="mb-4 text-[11px] text-[color:var(--color-cyber-muted)]">
          Invoque <span className="font-mono">slate-ctrl apply &lt;profile&gt;</span>{" "}
          sur le Slate. C'est l'agent local qui applique — pas le controller.
          Utile pour valider que le déploiement marche end-to-end.
        </p>

        {!installed && (
          <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
            Déploie + sync d'abord.
          </p>
        )}

        {installed && status.data && (
          <>
            <div className="flex flex-wrap gap-2">
              {status.data.remote_profiles.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => {
                    setApplyTarget(name);
                    apply.mutate(name);
                  }}
                  disabled={apply.isPending}
                  className={cn(
                    "inline-flex items-center gap-1 border px-3 py-2 text-[10px] font-bold uppercase tracking-[0.18em]",
                    status.data?.active === name
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                      : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-fg)]",
                    "disabled:opacity-50",
                  )}
                >
                  <ChevronRight className="h-3 w-3" />
                  {apply.isPending && applyTarget === name ? "apply…" : name}
                </button>
              ))}
            </div>

            {lastApply && (
              <div
                className={cn(
                  "mt-4 border p-3 text-[10px]",
                  lastApply.ok
                    ? "border-emerald-500/40 bg-emerald-500/5"
                    : "border-red-500/40 bg-red-500/5",
                )}
              >
                <div
                  className={cn(
                    "mb-2 flex items-center gap-1 font-bold",
                    lastApply.ok ? "text-emerald-300" : "text-red-300",
                  )}
                >
                  {lastApply.ok ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : (
                    <AlertTriangle className="h-3 w-3" />
                  )}
                  apply {lastApply.name}{" "}
                  {lastApply.ok ? "OK" : "FAILED"}
                </div>
                <pre className="whitespace-pre-wrap break-words font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                  {lastApply.output || "(no output)"}
                </pre>
              </div>
            )}

            {apply.isError && (
              <div className="mt-4 border border-red-500/40 bg-red-500/5 p-3 text-[10px] text-red-300">
                <AlertTriangle className="mr-1 inline h-3 w-3" />
                {errorMessage(apply.error)}
              </div>
            )}
          </>
        )}
      </section>

      {/* ── Slate → Controller push webhook ────────────────────────── */}
      <div className="mt-4">
        <WebhookPushPanel />
      </div>

      {/* ── Reset-button profile cycle ─────────────────────────────── */}
      <div className="mt-4">
        <ButtonCyclePanel />
      </div>

      {/* ── Manual refresh ────────────────────────────────────────── */}
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => status.refetch()}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          <RefreshCw className="h-3 w-3" />
          rafraîchir
        </button>
      </div>
    </div>
  );
}

function WebhookPushPanel() {
  const qc = useQueryClient();
  const deployMut = useMutation({
    mutationFn: deployAgentWebhook,
    onSettled: () =>
      qc.invalidateQueries({ queryKey: ["security", "lockout-status"] }),
  });
  const rotateMut = useMutation({
    mutationFn: rotateAgentWebhookSecret,
  });
  const last = deployMut.data ?? rotateMut.data;
  const lastErr = deployMut.error ?? rotateMut.error;
  return (
    <section className="cyber-card p-6">
      <header className="mb-3 flex items-center gap-2">
        <Upload className="cyber-glow h-4 w-4" />
        <h2 className="cyber-display text-lg">push webhook Slate → Controller</h2>
      </header>
      <p className="mb-4 text-[11px] leading-relaxed text-[color:var(--color-cyber-muted)]">
        Pousse l'agent <code>slate-ctrl-touchscreen-watcher</code> et son
        helper de signature HMAC sur le Slate. Une fois activ&eacute;, les
        &eacute;v&eacute;nements gl_screen (mauvais PIN, lockout) arrivent au controller en
        ~2&nbsp;s au lieu d'attendre le poll fallback 60&nbsp;s.
        <br />
        <br />
        <strong>Pré-requis</strong> : URL HTTPS dans{" "}
        <strong>Settings → URLs controller</strong> (ex.{" "}
        <code>https://controller.tonlab.local</code> ou{" "}
        <code>https://&lt;host&gt;.tail-xxx.ts.net</code>). Si le cert est
        sign&eacute; par ta CA interne (Settings → CA interne), elle sera
        pouss&eacute;e sur le Slate pour que <code>curl --cacert</code> valide.
        Les certs ts.net sont publiquement trusted, rien à faire.
      </p>

      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => deployMut.mutate()}
          disabled={deployMut.isPending || rotateMut.isPending}
          className="cyber-button px-4 py-2 text-xs"
        >
          {deployMut.isPending ? (
            <>
              <RefreshCw className="mr-2 inline h-3 w-3 animate-spin" />
              déploiement…
            </>
          ) : (
            <>
              <Upload className="mr-2 inline h-3 w-3" />
              activer / déployer le push
            </>
          )}
        </button>
        <button
          type="button"
          onClick={() => rotateMut.mutate()}
          disabled={rotateMut.isPending || deployMut.isPending}
          className="cyber-button-ghost px-4 py-2 text-xs"
          title="Génère un nouveau secret HMAC + re-provisionne le Slate. Ancien secret valide 30s."
        >
          {rotateMut.isPending ? (
            <>
              <RefreshCw className="mr-2 inline h-3 w-3 animate-spin" />
              rotation…
            </>
          ) : (
            <>
              <RefreshCw className="mr-2 inline h-3 w-3" />
              rotation du secret HMAC
            </>
          )}
        </button>
      </div>

      {last && (
        <div
          className={cn(
            "border p-3 text-[10px]",
            last.ok
              ? "border-emerald-500/40 bg-emerald-500/5 text-emerald-300"
              : "border-amber-500/40 bg-amber-500/5 text-amber-300",
          )}
        >
          <div className="mb-1 flex items-center gap-1">
            {last.ok ? (
              <CheckCircle2 className="h-3 w-3" />
            ) : (
              <AlertTriangle className="h-3 w-3" />
            )}
            <span className="font-mono uppercase tracking-wider">
              {last.ok ? "succès" : "partiel"} ·{" "}
              {last.pushed?.length ?? 0} push,{" "}
              {last.errors?.length ?? 0} erreur{(last.errors?.length ?? 0) > 1 ? "s" : ""}
            </span>
          </div>
          {(last.pushed ?? []).slice(0, 8).map((p, i) => (
            <div key={i} className="font-mono opacity-80">
              ▸ {p}
            </div>
          ))}
          {(last.errors ?? []).map((e, i) => (
            <div key={i} className="font-mono text-amber-300">
              ⚠ {e}
            </div>
          ))}
        </div>
      )}
      {lastErr && (
        <div className="border border-red-500/40 bg-red-500/5 p-3 text-[10px] text-red-300">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          {errorMessage(lastErr)}
        </div>
      )}
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between border-b border-[color:var(--color-cyber-border)] pb-2">
      <span className="cyber-label text-[9px]">{label}</span>
      <span className="text-[11px]">{value}</span>
    </div>
  );
}

function OpFeedback({
  name,
  isError,
  isSuccess,
  error,
  ok,
  lines,
  errors,
}: {
  name: string;
  isError: boolean;
  isSuccess: boolean;
  error: unknown;
  ok?: boolean;
  lines?: string[];
  errors?: string[];
}) {
  if (!isError && !isSuccess) return null;
  return (
    <div
      className={cn(
        "mt-3 border p-2 text-[10px]",
        isError || ok === false
          ? "border-red-500/40 bg-red-500/5 text-red-300"
          : "border-emerald-500/40 bg-emerald-500/5 text-emerald-300",
      )}
    >
      <div className="font-bold">
        {name}: {isError ? "ERREUR" : ok === false ? "FAILED" : "OK"}
      </div>
      {isError && <div className="mt-1">{errorMessage(error)}</div>}
      {lines && lines.length > 0 && (
        <ul className="mt-1 list-inside list-disc">
          {lines.map((l, i) => (
            <li key={i} className="font-mono">{l}</li>
          ))}
        </ul>
      )}
      {errors && errors.length > 0 && (
        <ul className="mt-1 list-inside list-disc">
          {errors.map((e, i) => (
            <li key={i} className="font-mono text-red-300">{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
