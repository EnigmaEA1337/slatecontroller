/**
 * Controller HTTPS sub-page — manages Tailscale Serve for THIS controller.
 *
 * The controller (this very app) lives in Docker. The host runs tailscaled.
 * We mount the host's tailscaled socket into the backend container so the
 * `tailscale` CLI inside can read/write the host's Serve config.
 *
 * Why Tailscale Serve and not Traefik + ACME:
 *   - cert auto-managed (Let's Encrypt, signed for *.tailnet.ts.net)
 *   - port 443 never has to be exposed on the public Internet
 *   - reachable from every device in the tailnet (iPhone Safari included)
 *   - zero renewal cron
 *
 * The UI renders a different layout depending on the snapshot's failure
 * flags so each error mode points the user at the exact next step.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  ExternalLink,
  Lock,
  PowerOff,
  RefreshCw,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import {
  ControllerHttpsState,
  disableControllerHttps,
  enableControllerHttps,
  getControllerHttpsState,
} from "@/api/controllerHttps";
import { errorMessage } from "@/lib/error-utils";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export default function ControllerHttps() {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "controller-https"],
    queryFn: getControllerHttpsState,
    // The cert read inside takes ~1-2s on cold path. Don't refetch on
    // window focus to avoid flicker; a manual Refresh button is exposed.
    refetchOnWindowFocus: false,
  });

  const enableMut = useMutation({
    mutationFn: enableControllerHttps,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["settings", "controller-https"] }),
  });
  const disableMut = useMutation({
    mutationFn: disableControllerHttps,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["settings", "controller-https"] }),
  });

  const state = q.data;
  const pending = enableMut.isPending || disableMut.isPending;

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Lock className="cyber-glow h-3 w-3" />
          {t("set_controller_https.subtitle")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("set_controller_https.title").toUpperCase()}
        >
          {t("set_controller_https.title").toUpperCase()}
        </h1>
        <p className="mt-2 max-w-2xl text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("set_controller_https.description")}
        </p>
      </header>

      {q.isLoading && (
        <div className="cyber-panel flex items-center gap-3 p-4 text-xs text-[color:var(--color-cyber-muted)]">
          <RefreshCw className="h-4 w-4 animate-spin" />
          {t("common.loading")}
        </div>
      )}

      {q.isError && (
        <div className="cyber-panel border-red-500/40 bg-red-500/5 p-4 text-xs text-red-300">
          Erreur lecture : {errorMessage(q.error)}
        </div>
      )}

      {state && <Body state={state} />}

      {state && (
        <div className="mt-6 flex flex-wrap gap-2">
          <button
            onClick={() => q.refetch()}
            disabled={q.isFetching}
            className="rounded border border-[color:var(--color-cyber-border)] px-3 py-1.5 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
          >
            <RefreshCw
              className={cn("mr-1 inline h-3 w-3", q.isFetching && "animate-spin")}
            />
            Rafraîchir
          </button>

          {state.cli_available &&
            state.daemon_reachable &&
            !state.https_enabled && (
              <button
                onClick={() => enableMut.mutate()}
                disabled={pending}
                className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs uppercase tracking-[0.15em] text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
              >
                <Lock className="mr-1 inline h-3 w-3" />
                {enableMut.isPending ? "Activation…" : "Activer HTTPS"}
              </button>
            )}

          {state.cli_available &&
            state.daemon_reachable &&
            state.https_enabled && (
              <button
                onClick={() => {
                  if (
                    confirm(
                      "Désactiver HTTPS sur ce controller ? Tu retomberas sur :5173 / :8000 en clair sur le tailnet.",
                    )
                  )
                    disableMut.mutate();
                }}
                disabled={pending}
                className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs uppercase tracking-[0.15em] text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
              >
                <PowerOff className="mr-1 inline h-3 w-3" />
                {disableMut.isPending ? "Désactivation…" : "Désactiver HTTPS"}
              </button>
            )}
        </div>
      )}

      {(enableMut.error || disableMut.error) && (
        <MutationError error={enableMut.error || disableMut.error} />
      )}
    </div>
  );
}

function Body({ state }: { state: ControllerHttpsState }) {
  // Branch on failure mode — each shows the right next-action.
  if (!state.cli_available) {
    return <CliMissing />;
  }
  if (!state.daemon_reachable) {
    return <DaemonUnreachable raw={state.raw_error} />;
  }
  // Plus loud than a "no cert" footer — if the admin feature is off,
  // nothing else can work. We still show the rest below it so the user
  // can see the hostname Tailscale would issue for.
  return (
    <>
      {state.feature_https_enabled_in_admin === false && (
        <AdminFeatureDisabled />
      )}
      <Configured state={state} />
    </>
  );
}

function AdminFeatureDisabled() {
  return (
    <section className="cyber-panel mb-6 border-amber-500/40 bg-amber-500/5 p-5 text-xs">
      <div className="mb-3 flex items-center gap-2 text-amber-300">
        <AlertTriangle className="h-4 w-4" />
        <span className="cyber-label">
          HTTPS pas activé dans ton tailnet admin
        </span>
      </div>
      <p className="text-[color:var(--color-cyber-dim)]">
        Tailscale a son propre toggle global "Enable HTTPS" qui doit être
        activé côté admin console avant que <code>tailscale cert</code> ne
        puisse émettre un certificat. Sans ça, activer HTTPS depuis cette
        page va échouer.
      </p>
      <a
        href="https://login.tailscale.com/admin/dns"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-3 inline-flex items-center gap-2 rounded border border-amber-500/50 bg-amber-500/10 px-3 py-1.5 text-xs uppercase tracking-[0.15em] text-amber-200 hover:bg-amber-500/20"
      >
        <ExternalLink className="h-3 w-3" />
        Ouvrir admin Tailscale DNS
      </a>
      <p className="mt-3 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
        Page admin → onglet DNS → bouton "Enable HTTPS" en bas. Une fois
        cliqué, reviens ici et clique "Rafraîchir".
      </p>
    </section>
  );
}

/* ---------- failure-mode panels ---------- */

function CliMissing() {
  return (
    <section className="cyber-panel border-amber-500/40 bg-amber-500/5 p-5 text-xs">
      <div className="mb-3 flex items-center gap-2 text-amber-300">
        <AlertTriangle className="h-4 w-4" />
        <span className="cyber-label">tailscale CLI introuvable côté backend</span>
      </div>
      <p className="text-[color:var(--color-cyber-dim)]">
        Le container backend n'a pas le binaire <code>tailscale</code> ou le
        socket tailscaled n'est pas monté. Rebuilds nécessaires :
      </p>
      <pre className="mt-3 overflow-x-auto rounded bg-black/40 p-3 text-[11px] text-[color:var(--color-cyber-dim)]">
        {`docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml build backend
docker compose -f docker-compose.dev.yml up -d`}
      </pre>
      <p className="mt-3 text-[color:var(--color-cyber-dim)]">
        Vérifie aussi que <code>/var/snap/tailscale/common/socket</code> existe
        sur ton host (ajuste le mount si Tailscale est installé en .deb au lieu
        du snap : adresse <code>/var/run/tailscale</code>).
      </p>
    </section>
  );
}

function DaemonUnreachable({ raw }: { raw: string | null }) {
  return (
    <section className="cyber-panel border-amber-500/40 bg-amber-500/5 p-5 text-xs">
      <div className="mb-3 flex items-center gap-2 text-amber-300">
        <ShieldOff className="h-4 w-4" />
        <span className="cyber-label">tailscaled injoignable depuis le container</span>
      </div>
      <p className="text-[color:var(--color-cyber-dim)]">
        Le CLI est installé mais le socket renvoie une erreur :
      </p>
      <pre className="mt-2 overflow-x-auto rounded bg-black/40 p-3 text-[11px] text-red-300">
        {raw || "(aucun détail)"}
      </pre>
      <p className="mt-3 text-[color:var(--color-cyber-dim)]">
        Vérifie que <code>tailscaled</code> tourne sur le host (
        <code>sudo systemctl status tailscaled</code> ou
        <code>snap services tailscale</code>), puis relance le container backend.
      </p>
    </section>
  );
}

/* ---------- main configured view ---------- */

function Configured({ state }: { state: ControllerHttpsState }) {
  const cert = state.cert;
  const hostname = state.tailnet_hostname || "—";

  return (
    <div className="flex flex-col gap-6">
      {/* Status banner */}
      <section
        className={cn(
          "cyber-panel flex items-start gap-4 p-5",
          state.https_enabled
            ? "border-emerald-500/40 bg-emerald-500/5"
            : "border-zinc-500/30",
        )}
      >
        {state.https_enabled ? (
          <CheckCircle2 className="mt-1 h-6 w-6 shrink-0 text-emerald-300" />
        ) : (
          <XCircle className="mt-1 h-6 w-6 shrink-0 text-zinc-400" />
        )}
        <div className="flex-1">
          <div className="cyber-label text-[10px]">
            {state.https_enabled ? "HTTPS actif" : "HTTPS inactif"}
          </div>
          {state.https_enabled && state.public_url ? (
            <a
              href={state.public_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-flex items-center gap-2 font-mono text-lg text-emerald-300 hover:underline"
            >
              {state.public_url}
              <ExternalLink className="h-4 w-4" />
            </a>
          ) : (
            <p className="mt-1 text-sm text-[color:var(--color-cyber-dim)]">
              Accès actuel uniquement via <code>:5173</code> /{" "}
              <code>:8000</code> en clair sur le tailnet.
            </p>
          )}
          <p className="mt-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            host tailnet :{" "}
            <code className="normal-case tracking-normal">{hostname}</code>
          </p>
        </div>
      </section>

      {/* Routes */}
      {state.https_enabled && state.routes.length > 0 && (
        <section className="cyber-panel p-5">
          <div className="cyber-label mb-3 text-[10px]">routes configurées</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[color:var(--color-cyber-muted)]">
                <th className="pb-2 text-left uppercase tracking-[0.15em]">
                  Chemin
                </th>
                <th className="pb-2 text-left uppercase tracking-[0.15em]">
                  Cible
                </th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {state.routes.map((r) => (
                <tr key={r.path} className="border-t border-[color:var(--color-cyber-border)]/40">
                  <td className="py-2 pr-4 text-[color:var(--color-cyber-accent)]">
                    {r.path}
                  </td>
                  <td className="py-2 text-[color:var(--color-cyber-dim)]">
                    {r.target}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Cert */}
      {cert && (
        <section className="cyber-panel p-5">
          <div className="cyber-label mb-3 text-[10px]">certificat actuel</div>
          <dl className="grid grid-cols-1 gap-2 text-xs md:grid-cols-2">
            <div>
              <dt className="text-[color:var(--color-cyber-muted)]">Émetteur</dt>
              <dd className="mt-1 font-mono text-[color:var(--color-cyber-dim)]">
                {cert.issuer || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-[color:var(--color-cyber-muted)]">Expiration</dt>
              <dd className="mt-1 font-mono text-[color:var(--color-cyber-dim)]">
                {cert.not_after ? new Date(cert.not_after).toLocaleString() : "—"}
                {cert.days_remaining !== null && (
                  <span
                    className={cn(
                      "ml-2 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-[0.15em]",
                      cert.days_remaining > 30
                        ? "bg-emerald-500/10 text-emerald-300"
                        : cert.days_remaining > 7
                          ? "bg-amber-500/10 text-amber-300"
                          : "bg-red-500/10 text-red-300",
                    )}
                  >
                    J-{cert.days_remaining}
                  </span>
                )}
              </dd>
            </div>
          </dl>
        </section>
      )}

      {/* Operator gate hint */}
      {!state.operator_set && (
        <OperatorHint />
      )}

      {/* Plain CLI fallback for power users / scripted setups */}
      <CliEquivalent state={state} />
    </div>
  );
}

function OperatorHint() {
  return (
    <section className="cyber-panel border-amber-500/40 bg-amber-500/5 p-5 text-xs">
      <div className="mb-3 flex items-center gap-2 text-amber-300">
        <AlertTriangle className="h-4 w-4" />
        <span className="cyber-label">opérateur Tailscale non configuré</span>
      </div>
      <p className="text-[color:var(--color-cyber-dim)]">
        Les lectures fonctionnent, mais activer/désactiver HTTPS depuis l'UI
        nécessite que ton utilisateur soit opérateur tailscale. Une seule
        commande à lancer sur le host :
      </p>
      <CopyableCommand cmd="sudo tailscale set --operator=$USER" />
    </section>
  );
}

function CliEquivalent({ state }: { state: ControllerHttpsState }) {
  const ready = state.cli_available && state.daemon_reachable && state.tailnet_hostname;
  if (!ready) return null;
  const enableSteps = state.https_enabled
    ? `tailscale serve reset`
    : `tailscale serve --bg --https=443 --set-path=/api http://localhost:8000
tailscale serve --bg --https=443 --set-path=/ http://localhost:5173`;
  return (
    <section className="cyber-panel p-5 text-xs">
      <div className="cyber-label mb-3 text-[10px]">équivalent CLI</div>
      <p className="text-[color:var(--color-cyber-dim)]">
        Si tu préfères piloter depuis ton terminal :
      </p>
      <CopyableCommand cmd={enableSteps} />
    </section>
  );
}

function CopyableCommand({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    await navigator.clipboard.writeText(cmd);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="mt-2 flex items-start gap-2 rounded bg-black/40 p-3">
      <pre className="flex-1 overflow-x-auto text-[11px] text-[color:var(--color-cyber-dim)]">
        {cmd}
      </pre>
      <button
        onClick={onCopy}
        className="shrink-0 rounded border border-[color:var(--color-cyber-border)] p-1 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        title="Copier"
      >
        <Copy className="h-3 w-3" />
      </button>
      {copied && (
        <span className="text-[10px] uppercase tracking-[0.15em] text-emerald-300">
          copié
        </span>
      )}
    </div>
  );
}

function MutationError({ error }: { error: unknown }) {
  // Surface operator hints from the backend's 400 detail blob.
  let msg = errorMessage(error);
  let hint = false;
  if (typeof error === "object" && error && "response" in error) {
    const detail = (error as { response?: { data?: { detail?: { message?: string; operator_hint?: boolean } } } })
      .response?.data?.detail;
    if (detail && typeof detail === "object") {
      msg = detail.message || msg;
      hint = !!detail.operator_hint;
    }
  }
  return (
    <div className="mt-4 cyber-panel border-red-500/40 bg-red-500/5 p-4 text-xs">
      <div className="flex items-start gap-2 text-red-300">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="flex-1">
          <div className="cyber-label">échec</div>
          <p className="mt-1 font-mono text-[color:var(--color-cyber-dim)]">
            {msg}
          </p>
          {hint && (
            <div className="mt-3">
              <p className="text-[color:var(--color-cyber-dim)]">
                Probable : tu n'as pas encore lancé la commande opérateur.
              </p>
              <CopyableCommand cmd="sudo tailscale set --operator=$USER" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
