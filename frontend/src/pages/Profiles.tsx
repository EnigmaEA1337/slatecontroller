import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Briefcase,
  ClipboardList,
  Copy,
  Home as HomeIcon,
  type LucideIcon,
  Palmtree,
  Pencil,
  Plus,
  Power,
  QrCode,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  activateProfile,
  deleteProfile,
  duplicateProfile,
  listProfiles,
  regenerateAllWallpapers,
} from "@/api/profiles";
import { listWifiSsids } from "@/api/wifi";
import PlanModal from "@/components/PlanModal";
import ProfileScoresBars from "@/components/ProfileScoresBars";
import WifiQRModal from "@/components/WifiQRModal";
import type { ProfileEnvelope } from "@/types/profile";
import type { WifiSsidPublic } from "@/types/wifi";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

const ICONS: Record<string, LucideIcon> = {
  briefcase: Briefcase,
  palmtree: Palmtree,
  search: Search,
  home: HomeIcon,
  "shield-alert": ShieldAlert,
};


function SourceBadge({ source }: { source: "template" | "user" }) {
  return (
    <span
      className={cn(
        "cyber-chip",
        source === "template" ? "cyber-chip-warn" : "cyber-chip-on",
      )}
    >
      {source}
    </span>
  );
}

function ProfileCard({
  envelope,
  wifiCatalog,
  onMutated,
  onShowPlan,
  onShowQR,
}: {
  envelope: ProfileEnvelope;
  wifiCatalog: WifiSsidPublic[];
  onMutated: () => void;
  onShowPlan: () => void;
  onShowQR: (ssid: WifiSsidPublic) => void;
}) {
  const navigate = useNavigate();
  const { profile, source, is_active } = envelope;
  const Icon: LucideIcon =
    (profile.icon ? ICONS[profile.icon] : undefined) ?? ShieldCheck;
  const enabledSsidRefs = profile.ssids.filter((s) => s.enabled);
  const enabledSsids = enabledSsidRefs.length;
  const totalSsids = profile.ssids.length;
  const enabledSsidsWithCatalog = enabledSsidRefs
    .map((ref) => wifiCatalog.find((w) => w.slug === ref.slug))
    .filter((w): w is WifiSsidPublic => w !== undefined);

  const activate = useMutation({
    mutationFn: () => activateProfile(profile.name),
    onSuccess: onMutated,
  });
  const del = useMutation({
    mutationFn: () => deleteProfile(profile.name),
    onSuccess: onMutated,
  });
  const dup = useMutation({
    mutationFn: () => duplicateProfile(profile.name, `${profile.name}-copy`),
    onSuccess: (env) => {
      onMutated();
      navigate(`/profiles/${env.profile.name}/edit`);
    },
  });

  const lastError = activate.error || del.error || dup.error;

  return (
    <article
      className={cn(
        "cyber-card p-5",
        is_active && "cyber-card-accent",
      )}
    >
      <header className="mb-4 flex items-start gap-3">
        <div className="cyber-glow flex h-10 w-10 shrink-0 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10">
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <h2 className="cyber-display cyber-glow text-base">
              {profile.name}
            </h2>
            <SourceBadge source={source} />
            {is_active && (
              <span className="cyber-chip cyber-chip-ok">active</span>
            )}
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
            {profile.description}
          </p>
        </div>
      </header>

      <div className="cyber-hatch mb-3 h-px w-full opacity-40" />

      <ProfileScoresBars scores={envelope.scores} />

      <div className="cyber-hatch my-3 h-px w-full opacity-40" />

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-xs">
        <dt className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-dim)]">
          VPN
        </dt>
        <dd>
          {profile.vpn.type === "none" ? (
            <span className="text-[color:var(--color-cyber-dim)]">—</span>
          ) : (
            <>
              <span className="cyber-glow font-mono uppercase">{profile.vpn.type}</span>
              {profile.vpn.client && (
                <span className="text-[color:var(--color-cyber-muted)]">
                  {" · "}
                  {profile.vpn.client}
                </span>
              )}
              {profile.vpn.kill_switch && (
                <span className="cyber-chip cyber-chip-warn ml-2">kill-switch</span>
              )}
            </>
          )}
        </dd>

        <dt className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-dim)]">
          SSIDs
        </dt>
        <dd>
          <span className="cyber-glow font-mono">
            {enabledSsids}/{totalSsids} actifs
          </span>
          {enabledSsidsWithCatalog.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {enabledSsidsWithCatalog.map((ssid) => (
                <button
                  key={ssid.slug}
                  type="button"
                  onClick={() => onShowQR(ssid)}
                  title={`QR pour ${ssid.ssid_name}`}
                  className="cyber-chip cursor-pointer hover:cyber-chip-on inline-flex items-center gap-1"
                >
                  <QrCode className="h-2.5 w-2.5" />
                  {ssid.slug}
                </button>
              ))}
            </div>
          )}
        </dd>

        <dt className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-dim)]">
          Firewall
        </dt>
        <dd>
          {profile.firewall.lockdown ? (
            <span className="cyber-chip cyber-chip-warn">lockdown</span>
          ) : (
            <span className="text-[color:var(--color-cyber-dim)]">normal</span>
          )}
        </dd>
      </dl>

      {lastError && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(lastError)}
        </p>
      )}

      <footer className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => {
            if (is_active) {
              // Re-apply on the same profile is idempotent server-side but
              // not free (full sync + run all handlers + likely reboot for
              // wifi layout changes). Confirm before firing so the operator
              // doesn't accidentally trigger a Slate reboot.
              if (!confirm(
                `Re-appliquer ${profile.name} ? Cela re-pousse la config sur le Slate et peut déclencher un reboot si les SSIDs ont changé.`,
              )) return;
            }
            activate.mutate();
          }}
          disabled={activate.isPending}
          className={cn(
            "flex items-center gap-1.5 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
            is_active
              ? "border-[color:var(--color-cyber-ok)] text-[color:var(--color-cyber-ok)] hover:bg-[color:var(--color-cyber-ok)]/10"
              : "border-[color:var(--color-cyber-accent-dim)] text-[color:var(--color-cyber-accent)] hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/6",
            "disabled:opacity-50",
          )}
          title={
            is_active
              ? "Re-pousser la conf de ce profil au Slate (utile après une modif de SSID, réseau, etc.)"
              : "Activer ce profil sur le Slate"
          }
        >
          <Power className="h-3 w-3" />
          {activate.isPending
            ? "Apply…"
            : is_active
              ? "Re-appliquer"
              : "Activer"}
        </button>
        <button
          type="button"
          onClick={onShowPlan}
          className="flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-fg)]"
          title="Dry-run : voir les opérations qui seraient appliquées au Slate"
        >
          <ClipboardList className="h-3 w-3" />
          Plan
        </button>
        <Link
          to={`/profiles/${encodeURIComponent(profile.name)}/edit`}
          className="flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-fg)]"
        >
          <Pencil className="h-3 w-3" />
          Éditer
        </Link>
        <button
          type="button"
          onClick={() => dup.mutate()}
          disabled={dup.isPending}
          className="flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-40"
          title="Dupliquer en tant que profil utilisateur"
        >
          <Copy className="h-3 w-3" />
          Dupliquer
        </button>
        {source === "user" && (
          <button
            type="button"
            onClick={() => {
              if (confirm(`Supprimer le profil "${profile.name}" ?`)) {
                del.mutate();
              }
            }}
            disabled={del.isPending}
            className="ml-auto flex items-center gap-1.5 border border-transparent px-2 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        )}
      </footer>
    </article>
  );
}

export default function Profiles() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery<ProfileEnvelope[]>({
    queryKey: ["profiles"],
    queryFn: listProfiles,
  });
  const wifiCatalogQuery = useQuery<WifiSsidPublic[]>({
    queryKey: ["wifi"],
    queryFn: listWifiSsids,
  });
  const [, forceRender] = useState(0);
  const [planFor, setPlanFor] = useState<string | null>(null);
  const [qrFor, setQrFor] = useState<WifiSsidPublic | null>(null);

  // QR-code-driven auto-activation: `/profiles?activate=NAME` triggers an
  // activation once the profile list is loaded. We strip the query param
  // right after firing so a page refresh doesn't re-activate.
  const [searchParams, setSearchParams] = useSearchParams();
  const autoActivate = searchParams.get("activate");
  const autoActivateMutation = useMutation({
    mutationFn: (name: string) => activateProfile(name),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["profiles", "active"] });
    },
  });
  useEffect(() => {
    if (!autoActivate || !data) return;
    const match = data.find((p) => p.profile.name === autoActivate);
    if (match && !match.is_active && !autoActivateMutation.isPending) {
      autoActivateMutation.mutate(autoActivate);
    }
    // Strip the param immediately so refresh doesn't re-fire it.
    const next = new URLSearchParams(searchParams);
    next.delete("activate");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoActivate, data]);

  const refresh = () => {
    // Both keys must invalidate: the profile list (is_active flags) AND the
    // /active singleton (used by Layout to drive the global wallpaper).
    queryClient.invalidateQueries({ queryKey: ["profiles"] });
    queryClient.invalidateQueries({ queryKey: ["profiles", "active"] });
    forceRender((n) => n + 1);
  };

  // Bulk-regenerate every profile's wallpapers (home + lock) with the
  // latest cyber theme. Overwrites slots — no confirm before, since the
  // generator is deterministic and the user can always rerun.
  const regenAll = useMutation({
    mutationFn: regenerateAllWallpapers,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      // Per-profile detail queries also need to refresh their wallpaper meta.
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
  });

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <ShieldCheck className="cyber-glow h-3 w-3" />
            contextual profiles
          </div>
          <h1
            className="cyber-display cyber-glitch text-4xl"
            data-text="PROFILS"
          >
            PROFILS
          </h1>
          <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            {data?.length ?? 0} profil(s) · template = livré · user = créé par toi
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => regenAll.mutate()}
            disabled={regenAll.isPending}
            title="Régénère le wallpaper home + lock de tous les profils avec le thème cyber actuel. Overwrite les slots existants."
            className={cn(
              "inline-flex items-center gap-2 border px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.18em]",
              "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]",
              "disabled:opacity-50",
            )}
          >
            <Sparkles className="h-3.5 w-3.5" />
            {regenAll.isPending ? "génération…" : "Régénérer les wallpapers"}
          </button>
          <Link
            to="/profiles/new"
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs"
          >
            <Plus className="h-3.5 w-3.5" />
            Nouveau profil
          </Link>
        </div>
      </header>

      {/* Feedback bar for the bulk regenerate operation. */}
      {(regenAll.isSuccess || regenAll.isError) && (
        <div
          className={cn(
            "mb-4 border p-3 text-[11px]",
            regenAll.isError
              ? "border-red-500/40 bg-red-500/5 text-red-300"
              : regenAll.data && regenAll.data.failed > 0
              ? "border-amber-500/40 bg-amber-500/5 text-amber-300"
              : "border-emerald-500/40 bg-emerald-500/5 text-emerald-300",
          )}
        >
          {regenAll.isError && <>Erreur : {errorMessage(regenAll.error)}</>}
          {regenAll.isSuccess && regenAll.data && (
            <>
              <span className="font-bold">
                {regenAll.data.regenerated} slot(s) régénéré(s)
              </span>
              {regenAll.data.pushed_active && (
                <>
                  {" · "}
                  {regenAll.data.pushed_active.ok ? (
                    <>
                      poussé sur le Slate (profil actif :{" "}
                      <span className="font-bold">
                        {regenAll.data.pushed_active.profile_name}
                      </span>
                      ) → home + lock mis à jour, gl_screen restart
                    </>
                  ) : (
                    <>
                      push Slate KO :{" "}
                      {regenAll.data.pushed_active.errors.join(" · ")}
                    </>
                  )}
                </>
              )}
              {!regenAll.data.pushed_active && (
                <>
                  {" · "}
                  <span className="text-[color:var(--color-cyber-muted)]">
                    aucun profil actif — active un profil pour pousser sur le Slate
                  </span>
                </>
              )}
              {regenAll.data.failed > 0 && (
                <>
                  {" · "}
                  <span className="font-bold">
                    {regenAll.data.failed} échec(s)
                  </span>
                  {": "}
                  {regenAll.data.errors
                    .map((e) => `${e.profile_name}/${e.kind}: ${e.error}`)
                    .join(" · ")}
                </>
              )}
            </>
          )}
        </div>
      )}

      {isLoading && <p className="cyber-label cyber-cursor">chargement</p>}

      {isError && (
        <div className="cyber-card cyber-card-accent p-4 text-sm text-[color:var(--color-cyber-accent)]">
          {errorMessage(error)}
        </div>
      )}

      {data && data.length === 0 && (
        <p className="text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
          ▸ Aucun profil — clique sur "Nouveau profil".
        </p>
      )}

      {data && data.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {data.map((env) => (
            <ProfileCard
              key={env.profile.name}
              envelope={env}
              wifiCatalog={wifiCatalogQuery.data ?? []}
              onMutated={refresh}
              onShowPlan={() => setPlanFor(env.profile.name)}
              onShowQR={(ssid) => setQrFor(ssid)}
            />
          ))}
        </div>
      )}

      {planFor && (
        <PlanModal
          profileName={planFor}
          onClose={() => setPlanFor(null)}
        />
      )}

      {qrFor && (
        <WifiQRModal
          slug={qrFor.slug}
          ssidName={qrFor.ssid_name}
          onClose={() => setQrFor(null)}
        />
      )}
    </div>
  );
}
