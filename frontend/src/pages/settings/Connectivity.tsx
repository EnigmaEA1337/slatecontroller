/**
 * Connectivity sub-page — controller URLs reachable from the Slate.
 *
 * The Slate calls BACK to the controller for things like:
 *   - the side-button hook (profile cycling)
 *   - future webhook-style events from the Slate
 *
 * Two URLs are configured because they apply in different contexts:
 *   - Tailscale URL (e.g., http://wraith-7.taild2bce8.ts.net:8000) — works
 *     in mobility, anywhere the tailnet reaches.
 *   - LAN URL (e.g., http://192.168.8.50:8000) — works only at home but
 *     lower latency.
 * The `preferred` choice dictates which the Slate hook tries first.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Globe,
  Network,
  Save,
} from "lucide-react";
import {
  ControllerUrls,
  getControllerUrls,
  updateControllerUrls,
} from "@/api/settings";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


export default function Connectivity() {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "controller-urls"],
    queryFn: getControllerUrls,
  });

  // Local draft — only POSTs when the user explicitly saves.
  const [tailscale, setTailscale] = useState("");
  const [lan, setLan] = useState("");
  const [preferred, setPreferred] = useState<"tailscale" | "lan">("tailscale");
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (q.data && !hydrated) {
      setTailscale(q.data.tailscale_url);
      setLan(q.data.lan_url);
      setPreferred(q.data.preferred);
      setHydrated(true);
    }
  }, [q.data, hydrated]);

  const save = useMutation({
    mutationFn: () =>
      updateControllerUrls({
        tailscale_url: tailscale,
        lan_url: lan,
        preferred,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "controller-urls"] });
    },
  });

  const dirty =
    q.data &&
    (tailscale !== q.data.tailscale_url ||
      lan !== q.data.lan_url ||
      preferred !== q.data.preferred);

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Globe className="cyber-glow h-3 w-3" />
          {t("set_connectivity.subtitle")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("set_connectivity.title").toUpperCase()}
        >
          {t("set_connectivity.title").toUpperCase()}
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("set_connectivity.description")}
        </p>
      </header>

      <section className="cyber-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <Network className="cyber-glow h-4 w-4" />
          <h2 className="cyber-display cyber-glow text-base">
            Controller URLs (Slate → Controller)
          </h2>
        </div>

        <p className="mb-5 text-[11px] text-[color:var(--color-cyber-muted)]">
          Le Slate appelle ces URLs pour notifier le controller — par exemple
          quand tu appuies sur le bouton physique latéral pour cycler les profils.
          Pas besoin si tu n'utilises pas de feature qui requiert un callback Slate.
        </p>

        {q.isLoading && (
          <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
            Chargement…
          </div>
        )}

        {q.data && (
          <div className="space-y-5">
            <Field
              label="URL Tailscale (mobilité)"
              hint="Exemple: http://wraith-7.taild2bce8.ts.net:8000 ou http://100.x.x.x:8000. Joignable depuis le Slate via le tailnet."
            >
              <input
                type="text"
                value={tailscale}
                onChange={(e) => setTailscale(e.target.value)}
                placeholder="http://controller-host.ts.net:8000"
                className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
              />
            </Field>

            <Field
              label="URL LAN (maison)"
              hint="Exemple: http://192.168.8.50:8000. Latence minimale, mais joignable uniquement quand le Slate est sur le même LAN que toi."
            >
              <input
                type="text"
                value={lan}
                onChange={(e) => setLan(e.target.value)}
                placeholder="http://192.168.x.x:8000"
                className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
              />
            </Field>

            <div className="space-y-2">
              <div className="cyber-label text-[10px]">
                Ordre de résolution préféré
              </div>
              <p className="text-[10px] text-[color:var(--color-cyber-muted)]">
                Quand les deux URLs sont configurées, le Slate essaie d'abord
                celle-ci. Fallback automatique sur l'autre si timeout.
              </p>
              <div className="flex">
                {(["tailscale", "lan"] as const).map((opt, idx) => (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => setPreferred(opt)}
                    className={cn(
                      "border px-4 py-2 text-[11px] font-bold uppercase tracking-[0.18em]",
                      idx > 0 && "border-l-0",
                      preferred === opt
                        ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                        : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                    )}
                  >
                    {opt === "tailscale" ? "Tailscale d'abord" : "LAN d'abord"}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2 pt-2">
              <button
                type="button"
                onClick={() => save.mutate()}
                disabled={!dirty || save.isPending}
                className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-4 py-2 text-[11px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
              >
                <Save className="h-3 w-3" />
                {save.isPending ? "Sauvegarde…" : "Enregistrer"}
              </button>
              {save.isSuccess && !dirty && (
                <span className="inline-flex items-center gap-1 text-[10px] text-emerald-300">
                  <CheckCircle2 className="h-3 w-3" />
                  enregistré
                </span>
              )}
            </div>

            {save.isError && (
              <div className="border border-red-500/40 bg-red-500/5 p-3 text-[10px] text-red-300">
                <AlertTriangle className="mr-1 inline h-3 w-3" />
                {errorMessage(save.error)}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="cyber-label text-[10px]">{label}</label>
      {hint && (
        <p className="text-[9px] text-[color:var(--color-cyber-muted)]">{hint}</p>
      )}
      <div className="mt-1">{children}</div>
    </div>
  );
}
