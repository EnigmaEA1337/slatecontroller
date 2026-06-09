import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Save, Wifi } from "lucide-react";
import {
  createProfile,
  getProfile,
  updateProfile,
} from "@/api/profiles";
import { listWifiSsids } from "@/api/wifi";
import { listNetworks } from "@/api/networks";
import WallpaperUploader from "@/components/WallpaperUploader";
import type {
  Profile,
  ProfileSSIDRef,
  TailscaleConnectionOverride,
  TailscaleHAOverride,
} from "@/types/profile";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

const EMPTY_PROFILE: Profile = {
  name: "",
  description: "",
  icon: null,
  color: "#ff3a52",
  vpn: { type: "none", client: null, kill_switch: false },
  tailscale: { enabled: false, admin_only: false, connection: null, ha: null },
  // AdGuard + DNS protection both moved to per-network config (Networks
  // page > DNS widget) — no per-profile blocks anymore.
  ssids: [],
  firewall: {
    lockdown: false,
    geoip_whitelist: [],
    block_telemetry: false,
    block_all_outbound: false,
  },
  logging: { level: "INFO", forward_to_siem: false },
};


// ---------------------------- reusable inputs ---------------------------- #

function SsidSelector({
  profile,
  onToggle,
  onNetworkChange,
}: {
  profile: Profile;
  onToggle: (slug: string, enabled: boolean) => void;
  onNetworkChange: (slug: string, networkSlug: string) => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["wifi"],
    queryFn: listWifiSsids,
  });
  const networksQ = useQuery({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });
  if (isLoading) {
    return (
      <p className="cyber-label cyber-cursor text-[10px]">chargement catalog</p>
    );
  }
  if (isError || !data) {
    return (
      <p className="text-[11px] text-[color:var(--color-cyber-accent)]">
        catalog Wi-Fi indisponible
      </p>
    );
  }
  if (data.length === 0) {
    return (
      <p className="text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
        ▸ aucun SSID dans le catalog · ajoute-en sur la page Radio
      </p>
    );
  }
  const refBySlug = new Map(profile.ssids.map((s) => [s.slug, s]));
  const networks = networksQ.data ?? [];
  return (
    <div className="space-y-2">
      {data.map((s) => {
        const ref = refBySlug.get(s.slug);
        const enabled = ref?.enabled ?? false;
        const networkSlug = ref?.network_slug ?? networks[0]?.slug ?? "lan";
        return (
          <div
            key={s.slug}
            className="flex flex-wrap items-center gap-3 border border-[color:var(--color-cyber-border)] p-2.5 transition hover:border-[color:var(--color-cyber-accent)]"
          >
            <label className="flex cursor-pointer items-center gap-3">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => onToggle(s.slug, e.target.checked)}
                className="h-4 w-4 accent-[color:var(--color-cyber-accent)]"
              />
              <Wifi className="h-3.5 w-3.5 text-[color:var(--color-cyber-accent)]" />
              <span className="font-mono text-xs">{s.slug}</span>
            </label>
            <span className="text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
              {(s.mlo ? "MLO " : "") + s.bands.map((b) => `${b}G`).join("/")} ·{" "}
              {s.security}
            </span>
            {s.client_isolation && (
              <span className="cyber-chip cyber-chip-warn">client iso</span>
            )}
            {/* L2→L3 binding : which network this SSID routes to in THIS
                profile. Only relevant when the SSID is enabled. */}
            <label className="ml-auto flex items-center gap-1.5 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
              réseau
              <select
                value={networkSlug}
                disabled={!enabled || networks.length === 0}
                onChange={(e) => onNetworkChange(s.slug, e.target.value)}
                className="cyber-input py-1 px-2 text-[11px] font-mono disabled:opacity-40"
              >
                {networks.length === 0 && <option value="lan">lan</option>}
                {networks.map((n) => (
                  <option key={n.slug} value={n.slug}>
                    {n.slug}
                  </option>
                ))}
              </select>
            </label>
          </div>
        );
      })}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="cyber-label mb-1.5 block">{label}</span>
      {children}
    </label>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="cyber-card p-5">
      <h3 className="cyber-label mb-4">{title}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)]">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 cursor-pointer accent-[color:var(--color-cyber-accent)]"
      />
      {label}
    </label>
  );
}

function CSVInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value.join(", "));
  useEffect(() => setDraft(value.join(", ")), [value]);
  return (
    <input
      type="text"
      value={draft}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const items = draft
          .split(",")
          .map((s) => s.trim())
          .filter((s) => s.length > 0);
        onChange(items);
      }}
      className="cyber-input w-full py-2 px-3 text-sm font-mono"
    />
  );
}

// ---------------------------- form ---------------------------- #

export default function ProfileForm() {
  const t = useT();
  const { name } = useParams<{ name?: string }>();
  const isEdit = Boolean(name);
  const navigate = useNavigate();

  const [profile, setProfile] = useState<Profile>(EMPTY_PROFILE);
  const [hydrated, setHydrated] = useState(!isEdit);

  // Load existing profile for edit mode
  const existing = useQuery({
    queryKey: ["profile", name],
    queryFn: () => getProfile(name!),
    enabled: isEdit,
  });
  useEffect(() => {
    if (existing.data && !hydrated) {
      setProfile(existing.data.profile);
      setHydrated(true);
    }
  }, [existing.data, hydrated]);

  const save = useMutation({
    mutationFn: () =>
      isEdit ? updateProfile(name!, profile) : createProfile(profile),
    onSuccess: () => navigate("/profiles"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  function patch<K extends keyof Profile>(key: K, value: Profile[K]) {
    setProfile((p) => ({ ...p, [key]: value }));
  }

  function patchSub<S extends keyof Profile>(
    section: S,
    sub: Partial<Profile[S]>,
  ) {
    setProfile((p) => ({ ...p, [section]: { ...(p[section] as object), ...sub } }));
  }

  function setSsidEnabled(slug: string, enabled: boolean) {
    setProfile((p) => {
      const existing = p.ssids.find((s) => s.slug === slug);
      if (existing) {
        return {
          ...p,
          ssids: p.ssids.map((s) => (s.slug === slug ? { ...s, enabled } : s)),
        };
      }
      const next: ProfileSSIDRef = { slug, enabled, network_slug: "lan" };
      return { ...p, ssids: [...p.ssids, next] };
    });
  }

  function setSsidNetwork(slug: string, network_slug: string) {
    setProfile((p) => {
      const existing = p.ssids.find((s) => s.slug === slug);
      if (existing) {
        return {
          ...p,
          ssids: p.ssids.map((s) =>
            s.slug === slug ? { ...s, network_slug } : s,
          ),
        };
      }
      // SSID not yet in the list (network picked before enabling) — add
      // it disabled with the chosen network.
      const next: ProfileSSIDRef = { slug, enabled: false, network_slug };
      return { ...p, ssids: [...p.ssids, next] };
    });
  }

  if (isEdit && !hydrated) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="cyber-label cyber-cursor">chargement</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <Link
          to="/profiles"
          className="cyber-label mb-4 inline-flex items-center gap-1.5 hover:cyber-glow"
        >
          <ArrowLeft className="h-3 w-3" />
          {t("profiles.title")}
        </Link>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={(isEdit
            ? t("profile_form.title_edit")
            : t("profile_form.title_new")
          ).toUpperCase()}
        >
          {(isEdit
            ? t("profile_form.title_edit")
            : t("profile_form.title_new")
          ).toUpperCase()}
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("profile_form.subtitle")}
        </p>
      </header>

      <form onSubmit={onSubmit} className="space-y-5">
        <Section title="général">
          <Field label="nom (slug, lowercase, [a-z0-9_-])">
            <input
              type="text"
              required
              disabled={isEdit}
              value={profile.name}
              onChange={(e) =>
                patch("name", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
              }
              className="cyber-input w-full py-2 px-3 text-sm font-mono disabled:opacity-50"
              placeholder="mon-profil"
            />
          </Field>
          <Field label="description">
            <input
              type="text"
              value={profile.description}
              onChange={(e) => patch("description", e.target.value)}
              className="cyber-input w-full py-2 px-3 text-sm"
              placeholder="Profil mission corporate avec kill switch strict"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="icon (lucide)">
              <input
                type="text"
                value={profile.icon ?? ""}
                onChange={(e) => patch("icon", e.target.value || null)}
                className="cyber-input w-full py-2 px-3 text-sm font-mono"
                placeholder="briefcase"
              />
            </Field>
            <Field label="color">
              <input
                type="color"
                value={profile.color ?? "#ff3a52"}
                onChange={(e) => patch("color", e.target.value)}
                className="h-9 w-full cursor-pointer border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]"
              />
            </Field>
          </div>
        </Section>

        {isEdit && existing.data && (
          <Section title="wallpapers du Slate (2 slots — nav + verrouillé)">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <WallpaperUploader
                profileName={existing.data.profile.name}
                kind="home"
                hasWallpaper={existing.data.wallpapers.home.has}
                fitMode={existing.data.wallpapers.home.fit_mode}
                version={existing.data.wallpapers.home.uploaded_at}
                showQr
              />
              <WallpaperUploader
                profileName={existing.data.profile.name}
                kind="lock"
                hasWallpaper={existing.data.wallpapers.lock.has}
                fitMode={existing.data.wallpapers.lock.fit_mode}
                version={existing.data.wallpapers.lock.uploaded_at}
              />
            </div>
          </Section>
        )}

        <Section title="vpn">
          <Field label="type">
            <select
              value={profile.vpn.type}
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                patchSub("vpn", { type: e.target.value as Profile["vpn"]["type"] })
              }
              className="cyber-input w-full py-2 px-3 text-sm font-mono"
            >
              <option value="none">none</option>
              <option value="wireguard">wireguard</option>
              <option value="openvpn">openvpn</option>
            </select>
          </Field>
          <Field label="client (nom de la config VPN sur le Slate)">
            <input
              type="text"
              value={profile.vpn.client ?? ""}
              onChange={(e) => patchSub("vpn", { client: e.target.value || null })}
              disabled={profile.vpn.type === "none"}
              className="cyber-input w-full py-2 px-3 text-sm font-mono disabled:opacity-40"
              placeholder="proton-fr-12"
            />
          </Field>
          <Checkbox
            label="kill switch"
            checked={profile.vpn.kill_switch}
            onChange={(v) => patchSub("vpn", { kill_switch: v })}
          />
        </Section>

        {/* Tor was removed from the per-profile schema. The daemon master
            switch + bridges + exit country live in TorSettings (global,
            see Réseau → Tor) ; routing decisions live per-network
            (NetworkRow.tor_route_mode). Nothing profile-shaped left. */}

        <Section title="tailscale">
          <Checkbox
            label="enabled"
            checked={profile.tailscale.enabled}
            onChange={(v) => patchSub("tailscale", { enabled: v })}
          />
          {/*
            Le flag `admin_only` per-profil a été retiré (2026-06-01) : la
            whitelist Settings → Tailnet admin est devenue la source unique.
            Si la whitelist est non-vide, les règles SC_FR_TS_ADMIN_*
            s'appliquent dans TOUS les profils ; si vide, pas de filtrage
            (anti self-DoS). Cf. backend `slate_agent/sync.py` pour la
            logique. Le champ reste dans le schéma profil pour
            back-compatibilité mais est ignoré au sync.
          */}

          {profile.tailscale.enabled && (
            <TailscaleOverridesEditor
              connection={profile.tailscale.connection}
              ha={profile.tailscale.ha}
              onConnectionChange={(connection) =>
                patchSub("tailscale", { connection })
              }
              onHAChange={(ha) => patchSub("tailscale", { ha })}
            />
          )}
        </Section>

        {/* AdGuard section removed — filtering / blocklists are now
            per-network (Networks page > DNS protection widget). */}

        <Section title="ssids">
          <p className="mb-3 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
            ▸ coche les SSIDs (depuis le catalog Wi-Fi) qui doivent être ON pour ce profil.{" "}
            <Link to="/wifi" className="cyber-glow underline-offset-4 hover:underline">
              gérer le catalog
            </Link>
          </p>
          <SsidSelector
            profile={profile}
            onToggle={setSsidEnabled}
            onNetworkChange={setSsidNetwork}
          />
        </Section>

        <Section title="firewall">
          <Checkbox
            label="lockdown"
            checked={profile.firewall.lockdown}
            onChange={(v) => patchSub("firewall", { lockdown: v })}
          />
          <Field label="geoip whitelist (csv country codes)">
            <CSVInput
              value={profile.firewall.geoip_whitelist}
              onChange={(geoip_whitelist) =>
                patchSub("firewall", { geoip_whitelist })
              }
              placeholder="FR, CH"
            />
          </Field>
          <Checkbox
            label="block telemetry"
            checked={profile.firewall.block_telemetry}
            onChange={(v) => patchSub("firewall", { block_telemetry: v })}
          />
          <Checkbox
            label="block all outbound (lockdown extrême)"
            checked={profile.firewall.block_all_outbound}
            onChange={(v) => patchSub("firewall", { block_all_outbound: v })}
          />
        </Section>

        <Section title="logging">
          <Field label="level">
            <select
              value={profile.logging.level}
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                patchSub("logging", { level: e.target.value as Profile["logging"]["level"] })
              }
              className="cyber-input w-full py-2 px-3 text-sm font-mono"
            >
              <option>DEBUG</option>
              <option>INFO</option>
              <option>WARNING</option>
              <option>ERROR</option>
              <option>CRITICAL</option>
            </select>
          </Field>
          <Checkbox
            label="forward to SIEM"
            checked={profile.logging.forward_to_siem}
            onChange={(v) => patchSub("logging", { forward_to_siem: v })}
          />
        </Section>

        {save.error && (
          <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
            {errorMessage(save.error)}
          </p>
        )}

        <div className="sticky bottom-4 flex gap-3">
          <button
            type="submit"
            disabled={save.isPending}
            className={cn(
              "cyber-button flex flex-1 items-center justify-center gap-2 px-4 py-3 text-sm",
            )}
          >
            <Save className="h-4 w-4" />
            {save.isPending
              ? "// saving…"
              : isEdit
                ? "Enregistrer ▸"
                : "Créer ▸"}
          </button>
          <Link
            to="/profiles"
            className="cyber-button-ghost flex items-center justify-center px-4 py-3 text-xs"
          >
            Annuler
          </Link>
        </div>
      </form>
    </div>
  );
}

// ---- Tailscale overrides editor ------------------------------------------
//
// Tri-state model for connection overrides: null = "inherit from current
// device prefs (don't touch on activate)" vs true/false = explicit value.
// We surface that via a "auto / on / off" segmented control rather than a
// plain checkbox so the user can revert to inherit without unchecking.

function TailscaleOverridesEditor({
  connection, ha,
  onConnectionChange, onHAChange,
}: {
  connection: TailscaleConnectionOverride | null;
  ha: TailscaleHAOverride | null;
  onConnectionChange: (v: TailscaleConnectionOverride | null) => void;
  onHAChange: (v: TailscaleHAOverride | null) => void;
}) {
  const [open, setOpen] = useState(connection !== null || ha !== null);

  function patchConn(patch: Partial<TailscaleConnectionOverride>) {
    const current: TailscaleConnectionOverride = connection ?? {
      accept_routes: null,
      accept_dns: null,
      advertise_routes: null,
      advertise_exit_node: null,
      exit_node: null,
      shields_up: null,
    };
    const next = { ...current, ...patch };
    // Collapse back to null if every field is null — keeps the YAML/DB clean.
    const allNull = Object.values(next).every(
      (v) => v === null || (Array.isArray(v) && v.length === 0 && next.advertise_routes === null),
    );
    onConnectionChange(allNull ? null : next);
  }

  function patchHA(patch: Partial<TailscaleHAOverride>) {
    const current: TailscaleHAOverride = ha ?? {
      enabled: null, candidates: null, failsafe_mode: null,
    };
    const next = { ...current, ...patch };
    const allNull = Object.values(next).every((v) => v === null);
    onHAChange(allNull ? null : next);
  }

  return (
    <div className="border-t border-[color:var(--color-cyber-border)] pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="cyber-label text-[10px] hover:text-[color:var(--color-cyber-fg)]"
      >
        {open ? "▾" : "▸"} overrides avancés (pushed on activate)
      </button>

      {open && (
        <div className="mt-3 space-y-3 text-[11px]">
          <p className="text-[color:var(--color-cyber-muted)]">
            Toutes les options ci-dessous sont <em>tri-state</em>:{" "}
            <span className="font-mono">auto</span> = ne pas toucher (hérite de la
            config actuelle du daemon),{" "}
            <span className="font-mono">on</span>/<span className="font-mono">off</span> = appliquer cette valeur à l'activation du profil.
          </p>

          <TriState
            label="accept_routes"
            value={connection?.accept_routes ?? null}
            onChange={(v) => patchConn({ accept_routes: v })}
            hint="Recevoir les subnet routes des peers"
          />
          <TriState
            label="accept_dns"
            value={connection?.accept_dns ?? null}
            onChange={(v) => patchConn({ accept_dns: v })}
            hint="Utiliser MagicDNS / nameservers du tailnet"
          />
          <TriState
            label="advertise_exit_node"
            value={connection?.advertise_exit_node ?? null}
            onChange={(v) => patchConn({ advertise_exit_node: v })}
            hint="Annoncer ce Slate comme exit-node pour les autres peers"
          />
          <TriState
            label="shields_up"
            value={connection?.shields_up ?? null}
            onChange={(v) => patchConn({ shields_up: v })}
            hint="Bloquer tout trafic entrant des peers du tailnet"
          />

          <Field label="advertise_routes (csv, vide = ne pas toucher)">
            <CSVInput
              value={connection?.advertise_routes ?? []}
              onChange={(routes) =>
                patchConn({
                  advertise_routes: routes.length === 0 ? null : routes,
                })
              }
              placeholder="10.137.42.0/24, 10.91.18.0/24, …"
            />
          </Field>

          <Field label="exit_node (hostname/IP du peer; vide = ne pas toucher)">
            <input
              type="text"
              value={connection?.exit_node ?? ""}
              onChange={(e) =>
                patchConn({
                  exit_node: e.target.value || null,
                })
              }
              placeholder="ui-etr-udm01-p"
              className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
            />
          </Field>

          {/* HA overrides */}
          <div className="border-t border-[color:var(--color-cyber-border)] pt-3">
            <div className="cyber-label text-[10px]">HA watchdog (failover + killswitch)</div>
            <div className="mt-2 space-y-2">
              <TriState
                label="ha.enabled"
                value={ha?.enabled ?? null}
                onChange={(v) => patchHA({ enabled: v })}
                hint="Active le watchdog d'exit-node failover"
              />
              <Field label="ha.candidates (csv, ordre = priorité)">
                <CSVInput
                  value={ha?.candidates ?? []}
                  onChange={(c) =>
                    patchHA({ candidates: c.length === 0 ? null : c })
                  }
                  placeholder="ui-etr-udm01-p, ui-etr-udm01-s"
                />
              </Field>
              <Field label="ha.failsafe_mode">
                <select
                  value={ha?.failsafe_mode ?? ""}
                  onChange={(e) =>
                    patchHA({
                      failsafe_mode:
                        (e.target.value || null) as TailscaleHAOverride["failsafe_mode"],
                    })
                  }
                  className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
                >
                  <option value="">(auto — ne pas toucher)</option>
                  <option value="fail_open">fail_open — drop exit-node → WAN</option>
                  <option value="keep">keep — préserve exit-node mort</option>
                </select>
              </Field>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TriState({
  label, value, onChange, hint,
}: {
  label: string;
  value: boolean | null;
  onChange: (v: boolean | null) => void;
  hint?: string;
}) {
  const opts: { v: boolean | null; label: string }[] = [
    { v: null, label: "auto" },
    { v: true, label: "on" },
    { v: false, label: "off" },
  ];
  return (
    <div className="flex items-center gap-3">
      <div className="min-w-[140px] text-[11px]">
        <div className="cyber-label text-[10px]">{label}</div>
        {hint && (
          <div className="text-[9px] text-[color:var(--color-cyber-muted)]">
            {hint}
          </div>
        )}
      </div>
      <div className="flex">
        {opts.map((o, idx) => {
          const active = value === o.v;
          return (
            <button
              key={idx}
              type="button"
              onClick={() => onChange(o.v)}
              className={cn(
                "border px-2 py-1 text-[10px] font-bold uppercase tracking-[0.18em]",
                idx > 0 && "border-l-0",
                active
                  ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                  : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
