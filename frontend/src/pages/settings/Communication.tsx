/**
 * Communication settings — what the controller may DO ON the Slate.
 *
 * Currently exposes one knob:
 *   show_screen_messages : when on, profile activations push a fullscreen
 *     "MISE A JOUR" overlay onto the Slate's front screen via direct
 *     framebuffer write. When off, activations run silently (no visible
 *     side effect on the panel).
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  MessageSquare,
  Save,
  Send,
} from "lucide-react";
import { api } from "@/api/client";
import {
  getSlateComms,
  updateSlateComms,
  type SlateComms,
} from "@/api/settings";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

type MessageKind = "status" | "action" | "error" | "ok";

function PreviewThumb({ kind, title, target }: { kind: MessageKind; title: string; target?: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const prev = useRef<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ title, kind });
    if (target) params.set("target", target);
    api.get(`/api/slate/screen/message/preview?${params}`, { responseType: "blob" })
      .then(({ data }) => {
        if (cancelled) return;
        if (prev.current) URL.revokeObjectURL(prev.current);
        const u = URL.createObjectURL(data);
        prev.current = u;
        setUrl(u);
      })
      .catch(() => { if (!cancelled) setUrl(null); });
    return () => {
      cancelled = true;
      if (prev.current) URL.revokeObjectURL(prev.current);
    };
  }, [kind, title, target]);

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="cyber-label text-[9px]">{kind.toUpperCase()}</div>
      {url ? (
        <img
          src={url}
          alt={`Preview ${kind}`}
          className="block border border-[color:var(--color-cyber-border)]"
          style={{ width: 240, height: 180 }}
        />
      ) : (
        <div
          className="flex items-center justify-center border border-[color:var(--color-cyber-border)] text-[9px] text-[color:var(--color-cyber-muted)]"
          style={{ width: 240, height: 180 }}
        >
          loading…
        </div>
      )}
    </div>
  );
}


export default function Communication() {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "slate-comms"],
    queryFn: getSlateComms,
  });

  const [show, setShow] = useState(true);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (q.data && !hydrated) {
      setShow(q.data.show_screen_messages);
      setHydrated(true);
    }
  }, [q.data, hydrated]);

  const save = useMutation({
    mutationFn: (patch: Partial<SlateComms>) => updateSlateComms(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "slate-comms"] });
    },
  });

  // Manual message — pushes a one-shot terminal-style overlay onto the
  // Slate's panel so the user can flash an arbitrary shell-style message
  // (`$> {title}`) from the controller UI.
  const [msgTitle, setMsgTitle] = useState("profile lockdown is loading");
  const [msgKind, setMsgKind] = useState<MessageKind>("status");
  const send = useMutation({
    mutationFn: async () => {
      const { data } = await api.post(
        "/api/slate/screen/message",
        {
          title: msgTitle.trim() || "hello from slate-controller",
          subtitle: "from slate-controller",
          target: msgKind,
          kind: msgKind,
          duration_seconds: 4.0,
        },
        { timeout: 30_000 },
      );
      return data;
    },
  });

  const dirty = q.data && show !== q.data.show_screen_messages;

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <MessageSquare className="cyber-glow h-3 w-3" />
          {t("set_communication.subtitle")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("set_communication.title").toUpperCase()}
        >
          {t("set_communication.title").toUpperCase()}
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("set_communication.description")}
        </p>
      </header>

      <section className="cyber-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <MessageSquare className="cyber-glow h-4 w-4" />
          <h2 className="cyber-display cyber-glow text-base">
            Messages écran du Slate
          </h2>
        </div>

        <p className="mb-5 text-[11px] text-[color:var(--color-cyber-muted)]">
          Quand le controller exécute une opération longue (activation profil,
          push wallpaper…), il peut afficher un overlay plein écran sur le Slate
          (kill gl_screen + write framebuffer + restart). Désactive ce toggle
          pour des activations 100% silencieuses — la panel garde l'UI GL.iNet
          inchangée pendant que les modifs se font en arrière-plan.
        </p>

        {q.isLoading && (
          <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
            Chargement…
          </div>
        )}

        {q.data && (
          <div className="space-y-5">
            {/* Toggle */}
            <label className="flex cursor-pointer items-start gap-3 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3 hover:border-[color:var(--color-cyber-accent)]">
              <input
                type="checkbox"
                checked={show}
                onChange={(e) => setShow(e.target.checked)}
                className="mt-1 accent-[color:var(--color-cyber-accent)]"
              />
              <div className="flex-1 text-[11px]">
                <div className="cyber-label">
                  Afficher les messages sur l'écran du Slate
                </div>
                <div className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
                  ON : tu vois "MISE A JOUR" plein écran pendant ~4s à chaque
                  activation de profil (le touch est inerte pendant ce temps).
                  OFF : aucune trace visible sur l'écran — les opérations
                  passent en silence.
                </div>
              </div>
            </label>

            {/* Save */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => save.mutate({ show_screen_messages: show })}
                disabled={!dirty || save.isPending}
                className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-4 py-2 text-[11px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
              >
                <Save className="h-3 w-3" />
                {save.isPending ? "sauvegarde…" : "enregistrer"}
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

            {/* Preview gallery — 4 kinds rendered server-side, shown here as
                static thumbnails so you see EXACTLY what the Slate panel will
                display. Cached font on the backend → fetch is fast. */}
            <div className="border-t border-[color:var(--color-cyber-border)] pt-5">
              <div className="cyber-label mb-1 text-[10px]">
                Aperçus du rendu — 4 variantes
              </div>
              <p className="mb-3 text-[10px] text-[color:var(--color-cyber-muted)]">
                Voilà à quoi ressemblent les messages selon le contexte sémantique.
                Frame "terminal" rouge + corner brackets + pill cible.
              </p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <PreviewThumb kind="status" title="MISE A JOUR" target="mission" />
                <PreviewThumb kind="action" title="PROFIL ACTIVE" target="home" />
                <PreviewThumb kind="error" title="ECHEC TAILSCALE" target="mission" />
                <PreviewThumb kind="ok" title="PROFIL OK" target="vacances" />
              </div>
            </div>

            {/* Send-message form — fires a one-shot terminal overlay regardless
                of the toggle. Free-form shell-style text, indépendamment du
                toggle "Afficher les messages". */}
            <div className="border-t border-[color:var(--color-cyber-border)] pt-5">
              <div className="cyber-label mb-2 text-[10px]">
                Envoyer un message
              </div>
              <p className="mb-3 text-[10px] text-[color:var(--color-cyber-muted)]">
                Pousse un message terminal style{" "}
                <span className="font-mono text-[color:var(--color-cyber-accent)]">$&gt;</span>{" "}
                pendant 4s sur l'écran physique. Indépendant du toggle ci-dessus.
              </p>

              <div className="space-y-3">
                {/* Free-form message body. Becomes `$> {value}` on the panel. */}
                <label className="block text-[10px]">
                  <span className="cyber-label mb-1 block text-[9px]">
                    Message (sera affiché comme <span className="font-mono">$&gt; …</span>)
                  </span>
                  <input
                    type="text"
                    value={msgTitle}
                    onChange={(e) => setMsgTitle(e.target.value)}
                    placeholder="profile lockdown is loading"
                    maxLength={120}
                    className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2 font-mono text-[11px] text-[color:var(--color-cyber-fg)] placeholder:text-[color:var(--color-cyber-muted)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
                  />
                </label>

                {/* Kind selector — controls the accent color of the terminal frame. */}
                <div className="flex flex-wrap gap-2">
                  {(["status", "action", "error", "ok"] as MessageKind[]).map((k) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setMsgKind(k)}
                      className={cn(
                        "border px-2 py-1 text-[9px] font-bold uppercase tracking-[0.16em]",
                        msgKind === k
                          ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                          : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                      )}
                    >
                      {k}
                    </button>
                  ))}
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => send.mutate()}
                    disabled={send.isPending || !msgTitle.trim()}
                    className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
                  >
                    <Send className="h-3 w-3" />
                    {send.isPending ? "envoi…" : "Envoyer un message"}
                  </button>
                  {send.isSuccess && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-emerald-300">
                      <CheckCircle2 className="h-3 w-3" />
                      envoyé
                    </span>
                  )}
                </div>
              </div>

              {send.isError && (
                <div className="mt-2 border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
                  <AlertTriangle className="mr-1 inline h-3 w-3" />
                  {errorMessage(send.error)}
                </div>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
