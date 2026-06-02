/**
 * TailnetAdmin sub-page — whitelist of tailnet peers that can reach the
 * Slate's admin surface (SSH, LuCI, AdGuard UI, slate-ctrl API).
 *
 * The list drives the firewall rules generated when a profile activates
 * with ``tailscale.admin_only=true``. Empty list = no enforcement (the
 * flag becomes a no-op until at least one IP is added — better than
 * locking the user out of his own tailnet).
 *
 * UX :
 *   - Peer picker fed from `/api/tailscale/status` (real peers, real
 *     hostnames, real IPs) so the user clicks 'add' next to a peer
 *     instead of typing 100.64.x.y by hand.
 *   - Manual fallback input for peers not in the live status (e.g. a
 *     phone that's currently offline but you still want whitelisted).
 *   - Save only persists ; deployment to the live Slate happens on the
 *     next /api/agent/deploy or profile activation.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Plus,
  Save,
  Shield,
  Trash2,
} from "lucide-react";
import {
  getTailnetAdminIps,
  updateTailnetAdminIps,
} from "@/api/settings";
import { ClickableHost } from "@/components/ClickableHost";
import { getTailscaleStatus } from "@/api/tailscale";
import { errorMessage } from "@/lib/error-utils";
import { cn } from "@/lib/utils";

// Mirror of `ADMIN_PORTS_TCP` in backend/app/slate_agent/sync.py. Kept in
// sync manually for the UI display ; the real source of truth is the
// backend constant which gets pushed to the Slate handler at sync time.
const ADMIN_PORTS = [22, 80, 443, 3000, 3443, 8000, 8080, 8443];

export default function TailnetAdmin() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "tailnet-admin-ips"],
    queryFn: getTailnetAdminIps,
  });
  // Pull the live tailnet view so the user can pick existing peers
  // instead of typing IPs. Stale-while-revalidate is fine ; if the
  // daemon's down the picker just falls back to manual input below.
  const ts = useQuery({
    queryKey: ["tailscale", "status"],
    queryFn: getTailscaleStatus,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const [draft, setDraft] = useState<string[]>([]);
  const [pendingInput, setPendingInput] = useState("");
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (q.data && !hydrated) {
      setDraft(q.data.admin_ips);
      setHydrated(true);
    }
  }, [q.data, hydrated]);

  const save = useMutation({
    mutationFn: () => updateTailnetAdminIps(draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "tailnet-admin-ips"] });
    },
  });

  function addIp(ip: string) {
    const v = ip.trim();
    if (!v || draft.includes(v)) return;
    setDraft([...draft, v]);
  }
  function addPending() {
    addIp(pendingInput);
    setPendingInput("");
  }
  function removeIp(ip: string) {
    setDraft(draft.filter((x) => x !== ip));
  }

  // Peers to show in the picker : skip Self (the Slate itself ; admin
  // surface is reached FROM peers, not the Slate's own tailnet IP) and
  // anything that's missing IPs. Sort by online-first then hostname so
  // the picker is friendly even with many offline devices.
  const pickablePeers = useMemo(() => {
    const peers = ts.data?.peers ?? [];
    return [...peers]
      .filter((p) => p.tailscale_ips.length > 0)
      .sort((a, b) => {
        if (a.online !== b.online) return a.online ? -1 : 1;
        return a.hostname.localeCompare(b.hostname);
      });
  }, [ts.data?.peers]);

  const dirty =
    q.data &&
    (draft.length !== q.data.admin_ips.length ||
      draft.some((x, i) => x !== q.data!.admin_ips[i]));

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Shield className="cyber-glow h-3 w-3" />
          settings · tailnet admin IPs
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text="TAILNET ADMIN"
        >
          TAILNET ADMIN
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          peers tailnet autorisés à atteindre l'admin du Slate
        </p>
      </header>

      <div className="cyber-card mb-5 border-l-2 border-l-[color:var(--color-cyber-accent)] p-4">
        <div className="flex items-start gap-3 text-[11px]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[color:var(--color-cyber-accent)]" />
          <div className="space-y-1">
            <p>
              <span className="cyber-glow">La whitelist est l'unique
              interrupteur du filtrage admin tailnet</span> — il n'y a plus
              de flag <code className="cyber-chip">admin_only</code>{" "}
              per-profil (déprécié 2026-06-01, ignoré au sync).
            </p>
            <p className="text-[color:var(--color-cyber-dim)]">
              ▸ <strong>Liste vide</strong> = pas de filtrage. N'importe quel
              peer tailnet peut atteindre l'admin du Slate. Safe-default
              anti self-lockout.
              <br />▸ <strong>Liste non-vide</strong> = filtrage actif{" "}
              <span className="cyber-glow">dans tous les profils</span> :
              seules les IPs listées peuvent atteindre l'admin.
            </p>
            <p className="text-[color:var(--color-cyber-dim)]">
              Le push vers le Slate est lazy : prend effet au prochain{" "}
              <code className="cyber-chip">/api/agent/deploy</code> ou
              activation de profil.
            </p>
          </div>
        </div>
      </div>

      <section className="cyber-card mb-6 p-5">
        <div className="cyber-label mb-3 flex items-center gap-2">
          <Shield className="h-3 w-3" /> ports admin gardés
        </div>
        <div className="flex flex-wrap gap-2">
          {ADMIN_PORTS.map((p) => (
            <span key={p} className="cyber-chip font-mono">
              tcp/{p}
            </span>
          ))}
        </div>
        <p className="mt-3 text-[10px] text-[color:var(--color-cyber-dim)]">
          ▸ <strong>22</strong> = SSH (dropbear) · <strong>80/443</strong> =
          GL.iNet UI (nginx) · <strong>8080/8443</strong> = LuCI (uhttpd) ·
          <strong>3000/3443</strong> = AdGuard Home UI (HTTP/HTTPS) ·
          <strong>8000</strong> = slate-ctrl API. Les ports DNS (53, 853,
          3053) et le peerapi Tailscale (34641) restent ouverts — ce sont
          des services tailnet, pas de l'admin.
        </p>
      </section>

      {/* ── Peer picker (auto from live tailnet) ─────────────────── */}
      <section className="cyber-card mb-6 p-5">
        <div className="cyber-label mb-3 flex items-center justify-between">
          <span>peers du tailnet</span>
          {ts.data && (
            <span className="text-[10px] normal-case tracking-normal text-[color:var(--color-cyber-dim)]">
              tailnet {ts.data.tailnet || "—"} · {pickablePeers.length} peer(s)
            </span>
          )}
        </div>

        {ts.isLoading && (
          <p className="text-[11px] italic text-[color:var(--color-cyber-dim)]">
            chargement des peers…
          </p>
        )}
        {ts.error && (
          <p className="text-[11px] italic text-[color:var(--color-cyber-dim)]">
            ▸ pas pu joindre `tailscale status` ({errorMessage(ts.error)})
            — utilise la saisie manuelle ci-dessous.
          </p>
        )}
        {ts.data && pickablePeers.length === 0 && (
          <p className="text-[11px] italic text-[color:var(--color-cyber-dim)]">
            ▸ aucun peer dans le tailnet pour l'instant
          </p>
        )}

        <ul className="space-y-1.5">
          {pickablePeers.map((p) => {
            const primary = p.tailscale_ips[0]!;
            const alreadyAdded = draft.includes(primary);
            return (
              <li
                key={p.dns_name || primary}
                className={cn(
                  "flex items-center gap-2 border px-3 py-1.5 text-xs",
                  alreadyAdded
                    ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
                    : "border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40",
                )}
              >
                <Circle
                  className={cn(
                    "h-2 w-2 shrink-0",
                    p.online
                      ? "fill-emerald-400 text-emerald-400"
                      : "fill-[color:var(--color-cyber-dim)] text-[color:var(--color-cyber-dim)]",
                  )}
                />
                <span className="flex-1 truncate font-mono">
                  {p.hostname || p.dns_name ? (
                    <ClickableHost value={p.hostname || p.dns_name} />
                  ) : (
                    <span className="text-[color:var(--color-cyber-muted)]">—</span>
                  )}
                  <span className="ml-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                    <ClickableHost value={primary} /> · {p.os || "?"}
                    {p.user ? ` · ${p.user}` : ""}
                  </span>
                </span>
                {alreadyAdded ? (
                  <span className="cyber-chip cyber-chip-on">
                    <CheckCircle2 className="mr-1 inline h-2.5 w-2.5" />
                    whitelisté
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => addIp(primary)}
                    title={`Ajouter ${primary} à la whitelist admin`}
                    className="cyber-button-ghost flex items-center gap-1 px-2 py-1 text-[10px]"
                  >
                    <Plus className="h-3 w-3" />
                    add
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      {/* ── Whitelist + manual fallback ─────────────────────────── */}
      <section className="cyber-card p-5">
        <div className="cyber-label mb-3">peers whitelistés</div>

        {draft.length === 0 && (
          <p className="mb-3 text-[11px] italic text-[color:var(--color-cyber-dim)]">
            ▸ aucune IP — admin_only sera un no-op à l'apply
          </p>
        )}

        <ul className="mb-4 space-y-1.5">
          {draft.map((ip) => {
            // Try to resolve which peer (if any) this IP belongs to so
            // the row shows the hostname instead of a raw 100.64.x.y.
            const peer = pickablePeers.find((p) =>
              p.tailscale_ips.includes(ip),
            );
            return (
              <li
                key={ip}
                className="flex items-center gap-2 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40 px-3 py-1.5 text-xs"
              >
                <Shield className="h-3 w-3 text-[color:var(--color-cyber-accent)]" />
                <span className="flex-1 font-mono">
                  {ip}
                  {peer && (
                    <span className="ml-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                      {peer.hostname}
                      {peer.os ? ` · ${peer.os}` : ""}
                    </span>
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => removeIp(ip)}
                  title="Retirer cette IP"
                  className="border border-transparent p-1 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </li>
            );
          })}
        </ul>

        <div className="flex gap-2">
          <input
            type="text"
            value={pendingInput}
            onChange={(e) => setPendingInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addPending();
              }
            }}
            placeholder="ajout manuel — ex 100.64.0.5 ou laptop.taild2bce8.ts.net"
            className="cyber-input flex-1 py-2 px-3 text-sm font-mono"
          />
          <button
            type="button"
            onClick={addPending}
            disabled={!pendingInput.trim()}
            className="cyber-button-ghost flex items-center gap-1.5 px-3 py-2 text-xs disabled:opacity-50"
          >
            <Plus className="h-3 w-3" />
            ajouter
          </button>
        </div>
      </section>

      {save.error && (
        <p className="cyber-chip cyber-chip-on mt-4 block !rounded-none px-3 py-2 text-xs">
          {errorMessage(save.error)}
        </p>
      )}

      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="cyber-button flex items-center gap-2 px-4 py-2.5 text-sm disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" />
          {save.isPending ? "// saving…" : "Enregistrer"}
        </button>
        {save.isSuccess && !dirty && (
          <span className="flex items-center gap-1.5 text-[11px] text-[color:var(--color-cyber-ok)]">
            <CheckCircle2 className="h-3 w-3" />
            sauvegardé
          </span>
        )}
        {dirty && (
          <span className="text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
            ▸ modifications non sauvegardées
          </span>
        )}
      </div>
    </div>
  );
}
