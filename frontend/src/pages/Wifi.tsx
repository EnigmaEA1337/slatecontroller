import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  Pencil,
  Plus,
  QrCode,
  Trash2,
  Users,
  Wifi as WifiIcon,
  X,
} from "lucide-react";
import {
  createWifiSsid,
  deleteWifiSsid,
  listWifiSsids,
  updateWifiSsid,
} from "@/api/wifi";
import PasswordGenerator from "@/components/PasswordGenerator";
import SsidSuggestionsPicker, {
  UniverseCombosPanel,
} from "@/components/SsidSuggestionsPicker";
import WifiPasswordModal from "@/components/WifiPasswordModal";
import WifiQRModal from "@/components/WifiQRModal";
import WifiSlateStatePanel from "@/components/WifiSlateStatePanel";
import {
  DEFAULT_ADVANCED,
  labelForBand,
  type WifiBand,
  type WifiPMF,
  type WifiSecurity,
  type WifiSsidAdvanced,
  type WifiSsidPublic,
  type WifiSsidWrite,
} from "@/types/wifi";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

const ALL_BANDS: WifiBand[] = ["2", "5", "6"];
const SECURITIES: WifiSecurity[] = [
  "WPA3-SAE",
  "WPA3-PSK",
  "WPA2-PSK",
  "WPA2-WPA3-Mixed",
  "open",
];


// ---------------------------- Form ---------------------------- #

interface FormProps {
  initialSlug?: string;
  initial?: WifiSsidPublic;
  onClose: () => void;
}

function WifiForm({ initialSlug, initial, onClose }: FormProps) {
  const isEdit = Boolean(initial);
  const [slug, setSlug] = useState(initialSlug ?? "");
  const [ssidName, setSsidName] = useState(initial?.ssid_name ?? "");
  // Multi-band : default to 5 GHz only for a fresh SSID. The user
  // ticks 2.4 to add range / legacy compat, or 6 for Wi-Fi 6E/7 clients.
  const [bands, setBands] = useState<Set<WifiBand>>(
    new Set<WifiBand>(initial?.bands ?? ["5"]),
  );
  // MLO (Wi-Fi 7 Multi-Link) — flag persists but the handler currently
  // refuses to deploy it, so we disable the checkbox in the form below.
  const [mlo, setMlo] = useState(initial?.mlo ?? false);
  const [security, setSecurity] = useState<WifiSecurity>(
    initial?.security ?? "WPA3-SAE",
  );
  const [clientIsolation, setClientIsolation] = useState(
    initial?.client_isolation ?? false,
  );
  const [hidden, setHidden] = useState(initial?.hidden ?? false);
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [changePassword, setChangePassword] = useState(!isEdit);
  const [advanced, setAdvanced] = useState<WifiSsidAdvanced>(
    initial?.advanced ?? DEFAULT_ADVANCED,
  );
  const [showAdvanced, setShowAdvanced] = useState(false);

  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: () => {
      const isOpen = security === "open";
      const body: WifiSsidWrite = {
        ssid_name: ssidName,
        bands: ALL_BANDS.filter((b) => bands.has(b)),
        mlo,
        security,
        password: isOpen ? "" : changePassword ? password : null,
        client_isolation: clientIsolation,
        hidden,
        notes,
        advanced,
      };
      return isEdit
        ? updateWifiSsid(slug, body)
        : createWifiSsid({ ...body, slug });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["wifi"] });
      onClose();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit.mutate();
  }

  return (
    <form
      onSubmit={onSubmit}
      className="cyber-card cyber-card-accent space-y-4 p-5"
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="cyber-display cyber-glow text-lg">
          {isEdit ? `EDIT SSID · ${slug}` : "NEW SSID"}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="cyber-label mb-1.5 block">slug</span>
          <input
            type="text"
            required
            disabled={isEdit}
            value={slug}
            onChange={(e) =>
              setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
            }
            placeholder="missionpro"
            className="cyber-input w-full py-2 px-3 text-sm font-mono disabled:opacity-50"
          />
        </label>
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">ssid (broadcast)</span>
          <input
            type="text"
            required
            maxLength={32}
            value={ssidName}
            onChange={(e) => setSsidName(e.target.value)}
            placeholder="MissionPro"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
          <SsidSuggestionsPicker
            currentValue={ssidName}
            onPick={(name) => setSsidName(name)}
          />
        </label>
        <div className="block">
          <span className="cyber-label mb-1.5 block">bandes</span>
          <div className="flex flex-wrap gap-2">
            {ALL_BANDS.map((b) => {
              const on = bands.has(b);
              return (
                <label
                  key={b}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 border px-2.5 py-1.5 text-xs font-mono",
                    on
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-fg)]"
                      : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={(e) => {
                      const next = new Set(bands);
                      if (e.target.checked) next.add(b);
                      else next.delete(b);
                      // Refuse to leave bands empty — backend rejects it.
                      if (next.size === 0) return;
                      setBands(next);
                    }}
                    className="h-3 w-3 accent-[color:var(--color-cyber-accent)]"
                  />
                  {labelForBand(b)}
                </label>
              );
            })}
          </div>
          <label
            className={cn(
              "mt-2 flex items-start gap-2 text-[11px]",
              "opacity-60",
            )}
            title="MLO — Wi-Fi 7 Multi-Link Operation. Handler agent pas encore prêt."
          >
            <input
              type="checkbox"
              checked={mlo}
              disabled
              onChange={(e) => setMlo(e.target.checked)}
              className="mt-0.5 h-3 w-3 accent-[color:var(--color-cyber-accent)]"
            />
            <span>
              MLO (Wi-Fi 7 Multi-Link)
              <span className="ml-2 cyber-chip">coming soon</span>
              <span className="block text-[10px] text-[color:var(--color-cyber-dim)]">
                ▸ regroupe les bandes sous un seul MLD pour les clients Wi-Fi 7
              </span>
            </span>
          </label>
        </div>
        <label className="block">
          <span className="cyber-label mb-1.5 block">security</span>
          <select
            value={security}
            onChange={(e) => setSecurity(e.target.value as WifiSecurity)}
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          >
            {SECURITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* No network selector here : the SSID → network (bridge) binding
          is a per-profile decision now (Profiles page). The radio
          catalog only defines the L2 access (name/bands/security/PSK). */}
      <p className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40 px-3 py-2 text-[10px] text-[color:var(--color-cyber-dim)]">
        ▸ Le réseau (bridge/subnet) auquel ce SSID se rattache se choisit
        dans chaque <span className="cyber-glow-soft">profil</span> — un SSID
        peut router vers des réseaux différents selon le contexte.
      </p>

      {security !== "open" && (
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="cyber-label">password</span>
            {isEdit && (
              <label className="flex items-center gap-2 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
                <input
                  type="checkbox"
                  checked={changePassword}
                  onChange={(e) => setChangePassword(e.target.checked)}
                  className="h-3 w-3 accent-[color:var(--color-cyber-accent)]"
                />
                modifier
              </label>
            )}
          </div>
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[color:var(--color-cyber-dim)]" />
            <input
              type={showPw ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isEdit && !changePassword}
              placeholder={
                isEdit && !changePassword
                  ? "•••••••• (laisser tel quel)"
                  : "saisir le PSK"
              }
              className="cyber-input w-full py-2 pl-9 pr-9 text-sm font-mono disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => setShowPw((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-[color:var(--color-cyber-dim)] hover:text-[color:var(--color-cyber-accent)]"
            >
              {showPw ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
          {(!isEdit || changePassword) && (
            <div className="mt-2">
              <PasswordGenerator
                onGenerate={(pw) => {
                  setPassword(pw);
                  setShowPw(true);
                }}
              />
            </div>
          )}
        </div>
      )}

      <label className="flex items-center gap-2 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)]">
        <input
          type="checkbox"
          checked={clientIsolation}
          onChange={(e) => setClientIsolation(e.target.checked)}
          className="h-4 w-4 accent-[color:var(--color-cyber-accent)]"
        />
        client isolation (clients du SSID ne se voient pas entre eux)
      </label>

      <label className="flex items-start gap-2 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)]">
        <input
          type="checkbox"
          checked={hidden}
          onChange={(e) => setHidden(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-[color:var(--color-cyber-accent)]"
        />
        <span>
          SSID caché — omis des beacons
          <span className="ml-2 block normal-case tracking-normal text-[10px] text-[color:var(--color-cyber-dim)]">
            ▸ ne masque PAS le réseau : les clients leakent toujours le nom
            via probe requests, BSSID visible. Cosmétique uniquement.
          </span>
        </span>
      </label>

      <label className="block">
        <span className="cyber-label mb-1.5 block">notes</span>
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="usage typique"
          className="cyber-input w-full py-2 px-3 text-sm"
        />
      </label>

      <AdvancedSection
        open={showAdvanced}
        onToggle={() => setShowAdvanced((v) => !v)}
        value={advanced}
        onChange={setAdvanced}
      />

      {submit.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(submit.error)}
        </p>
      )}

      <div className="flex gap-3">
        <button
          type="submit"
          disabled={submit.isPending}
          className="cyber-button flex-1 px-4 py-2.5 text-sm"
        >
          {submit.isPending ? "// saving…" : isEdit ? "Enregistrer ▸" : "Créer ▸"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="cyber-button-ghost px-4 py-2.5 text-xs"
        >
          Annuler
        </button>
      </div>
    </form>
  );
}

// ---------------------------- Advanced section ---------------------------- #

/** 8 MTK-specific UCI toggles exposed under a collapsible "Avancé" pane.
 *  Each field has a short hint explaining the tradeoff so the operator
 *  doesn't have to look up 802.11 letters every time. */
function AdvancedSection({
  open,
  onToggle,
  value,
  onChange,
}: {
  open: boolean;
  onToggle: () => void;
  value: WifiSsidAdvanced;
  onChange: (v: WifiSsidAdvanced) => void;
}) {
  const dirty = JSON.stringify(value) !== JSON.stringify(DEFAULT_ADVANCED);
  const set = <K extends keyof WifiSsidAdvanced>(
    k: K, v: WifiSsidAdvanced[K],
  ) => onChange({ ...value, [k]: v });
  return (
    <div className="border border-[color:var(--color-cyber-border)]/60 rounded-sm">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between px-3 py-2 text-xs uppercase tracking-wider text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
      >
        <span className="flex items-center gap-2">
          <span>{open ? "▾" : "▸"}</span>
          <span>Avancé · 8 options MTK</span>
          {dirty && (
            <span className="cyber-chip text-[9px] text-[color:var(--color-cyber-accent)]">
              modifié
            </span>
          )}
        </span>
        <span className="text-[10px] font-mono">
          {value.pmf} · DTIM {value.dtim_period}
        </span>
      </button>
      {open && (
        <div className="px-3 py-3 border-t border-[color:var(--color-cyber-border)]/40 space-y-3">
          <Field
            label="PMF (802.11w · Protected Management Frames)"
            hint="required = WPA3 strict, bloque les clients pré-WPA3 ; optional = laisse les clients choisir"
          >
            <select
              value={value.pmf}
              onChange={(e) => set("pmf", e.target.value as WifiPMF)}
              className="cyber-input w-full text-xs"
            >
              <option value="disabled">disabled</option>
              <option value="optional">optional</option>
              <option value="required">required</option>
            </select>
          </Field>

          <BoolField
            label="FT (802.11r · Fast Transition)"
            hint="Roaming rapide entre APs. Peut casser des clients IoT mal calibrés."
            checked={value.ft_802_11r}
            onChange={(v) => set("ft_802_11r", v)}
          />
          <BoolField
            label="RRM (802.11k · Neighbor Reports)"
            hint="L'AP envoie aux clients la liste des APs voisins → roaming + intelligent"
            checked={value.rrm_802_11k}
            onChange={(v) => set("rrm_802_11k", v)}
          />
          <BoolField
            label="BTM (802.11v · BSS Transition)"
            hint="L'AP peut suggérer à un client de basculer vers un autre AP — utile en mesh"
            checked={value.btm_802_11v}
            onChange={(v) => set("btm_802_11v", v)}
          />

          <Field
            label="DTIM period"
            hint="Beacons entre chaque délivrance multicast. Plus haut = clients économisent batterie, plus de latence multicast."
          >
            <input
              type="number"
              min={1}
              max={10}
              value={value.dtim_period}
              onChange={(e) =>
                set("dtim_period", Math.max(1, Math.min(10, Number(e.target.value) || 2)))
              }
              className="cyber-input w-24 text-xs"
            />
          </Field>

          <BoolField
            label="WMM (Wireless Multimedia · QoS)"
            hint="Priorité voix/vidéo. Obligatoire pour WPA3 — laisse activé."
            checked={value.wmm}
            onChange={(v) => set("wmm", v)}
          />
          <BoolField
            label="Proxy ARP"
            hint="L'AP répond aux ARP au nom des clients connus → moins de broadcast en l'air"
            checked={value.proxy_arp}
            onChange={(v) => set("proxy_arp", v)}
          />
          <BoolField
            label="WDS (Wireless Distribution System)"
            hint="Mode bridge wireless. Rarement nécessaire avec MLO/mesh moderne."
            checked={value.wds}
            onChange={(v) => set("wds", v)}
          />

          {dirty && (
            <button
              type="button"
              onClick={() => onChange(DEFAULT_ADVANCED)}
              className="text-[10px] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] underline"
            >
              ↺ remettre les défauts
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Field({
  label, hint, children,
}: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="cyber-label block text-[10px] mb-1">{label}</span>
      {children}
      {hint && (
        <span className="block mt-1 text-[9px] text-[color:var(--color-cyber-muted)] italic">
          {hint}
        </span>
      )}
    </label>
  );
}

function BoolField({
  label, hint, checked, onChange,
}: {
  label: string; hint?: string;
  checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="cyber-checkbox mt-0.5 shrink-0"
      />
      <span className="text-xs">
        {label}
        {hint && (
          <span className="block text-[9px] text-[color:var(--color-cyber-muted)] italic">
            {hint}
          </span>
        )}
      </span>
    </label>
  );
}

// ---------------------------- Table row ---------------------------- #

// One SSID = one table row. Network/bridge mapping is intentionally NOT
// shown here — that binding is a per-profile decision (Profiles page),
// not a property of the radio catalog entry.
function WifiRow({
  ssid,
  onEdit,
  onDeleted,
  onShowQR,
  onShowPassword,
}: {
  ssid: WifiSsidPublic;
  onEdit: () => void;
  onDeleted: () => void;
  onShowQR: () => void;
  onShowPassword: () => void;
}) {
  const del = useMutation({
    mutationFn: () => deleteWifiSsid(ssid.slug),
    onSuccess: onDeleted,
  });

  return (
    <tr className="border-b border-[color:var(--color-cyber-border)] hover:bg-[color:var(--color-cyber-bg-2)]/40">
      {/* SSID + slug */}
      <td className="px-3 py-2.5 align-top">
        <div className="cyber-glow-soft font-mono text-sm">{ssid.ssid_name}</div>
        <div className="text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)]">
          {ssid.slug}
        </div>
        {ssid.notes && (
          <div className="mt-1 max-w-[22ch] truncate text-[10px] italic text-[color:var(--color-cyber-dim)]" title={ssid.notes}>
            {ssid.notes}
          </div>
        )}
      </td>
      {/* Bands + MLO */}
      <td className="px-3 py-2.5 align-top">
        <div className="flex flex-wrap gap-1">
          {ssid.bands.map((b) => (
            <span key={b} className="cyber-chip">
              {labelForBand(b)}
            </span>
          ))}
          {ssid.mlo && (
            <span className="cyber-chip cyber-chip-on" title="Wi-Fi 7 Multi-Link Operation">
              MLO
            </span>
          )}
        </div>
      </td>
      {/* Security */}
      <td className="px-3 py-2.5 align-top">
        <span className="font-mono text-[11px]">{ssid.security}</span>
      </td>
      {/* Flags : PSK/open, client iso, hidden */}
      <td className="px-3 py-2.5 align-top">
        <div className="flex flex-wrap gap-1">
          {ssid.has_password ? (
            <span className="cyber-chip cyber-chip-on">PSK</span>
          ) : (
            <span className="cyber-chip">open</span>
          )}
          {ssid.client_isolation && (
            <span className="cyber-chip cyber-chip-warn" title="Clients du SSID isolés entre eux">
              client iso
            </span>
          )}
          {ssid.hidden && (
            <span className="cyber-chip" title="SSID omis des beacons (cosmétique)">
              caché
            </span>
          )}
        </div>
      </td>
      {/* Actions */}
      <td className="px-3 py-2.5 align-top">
        <div className="flex justify-end gap-1">
          <button
            type="button"
            onClick={onShowQR}
            title="QR code WiFi"
            className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <QrCode className="h-3.5 w-3.5" />
          </button>
          {ssid.has_password && (
            <button
              type="button"
              onClick={onShowPassword}
              title="Révéler le mot de passe"
              className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
            >
              <KeyRound className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={onEdit}
            className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => {
              if (confirm(`Supprimer le SSID "${ssid.slug}" ?`)) del.mutate();
            }}
            disabled={del.isPending}
            title={del.error ? errorMessage(del.error) : "Supprimer"}
            className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------- Page ---------------------------- #

export default function Wifi() {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<WifiSsidPublic | null>(null);
  const [creating, setCreating] = useState(false);
  const [qrFor, setQrFor] = useState<WifiSsidPublic | null>(null);
  const [pwFor, setPwFor] = useState<WifiSsidPublic | null>(null);

  const ssids = useQuery({ queryKey: ["wifi"], queryFn: listWifiSsids });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["wifi"] });
  const closeForm = () => {
    setEditing(null);
    setCreating(false);
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <WifiIcon className="cyber-glow h-3 w-3" />
            {t(
              (ssids.data?.length ?? 0) === 1
                ? "wifi.subtitle"
                : "wifi.subtitle_plural",
              { n: ssids.data?.length ?? 0 },
            )}
          </div>
          <h1
            className="cyber-display cyber-glitch text-4xl"
            data-text={t("wifi.title").toUpperCase()}
          >
            {t("wifi.title").toUpperCase()}
          </h1>
          <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            {t("wifi.description")}
          </p>
        </div>
        {!creating && !editing && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("wifi.new")}
          </button>
        )}
      </header>

      {creating && (
        <section className="mb-6">
          <WifiForm onClose={closeForm} />
        </section>
      )}

      {editing && (
        <section className="mb-6">
          <WifiForm
            initialSlug={editing.slug}
            initial={editing}
            onClose={closeForm}
          />
        </section>
      )}

      {ssids.isLoading && (
        <p className="cyber-label cyber-cursor">chargement</p>
      )}

      {ssids.isError && (
        <div className="cyber-card cyber-card-accent p-4 text-sm text-[color:var(--color-cyber-accent)]">
          {errorMessage(ssids.error)}
        </div>
      )}

      {ssids.data && ssids.data.length === 0 && (
        <p className="text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
          ▸ aucun SSID. Crée le premier !
        </p>
      )}

      {ssids.data && ssids.data.length > 0 && (
        <div className="cyber-card overflow-x-auto p-0">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-[color:var(--color-cyber-border-strong)] text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                <th className="px-3 py-2 font-semibold">SSID</th>
                <th className="px-3 py-2 font-semibold">Bandes</th>
                <th className="px-3 py-2 font-semibold">Sécurité</th>
                <th className="px-3 py-2 font-semibold">Flags</th>
                <th className="px-3 py-2 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {ssids.data.map((s) => (
                <WifiRow
                  key={s.slug}
                  ssid={s}
                  onEdit={() => {
                    setCreating(false);
                    setEditing(s);
                  }}
                  onDeleted={refresh}
                  onShowQR={() => setQrFor(s)}
                  onShowPassword={() => setPwFor(s)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {qrFor && (
        <WifiQRModal
          slug={qrFor.slug}
          ssidName={qrFor.ssid_name}
          onClose={() => setQrFor(null)}
        />
      )}

      {pwFor && (
        <WifiPasswordModal
          slug={pwFor.slug}
          ssidName={pwFor.ssid_name}
          onClose={() => setPwFor(null)}
        />
      )}

      <div className="mt-8">
        <WifiSlateStatePanel />
      </div>

      <UniverseCombosPanel />

      {/* Tiny didactic footer */}
      <footer className="mt-10 cyber-card p-5">
        <h3 className="cyber-label mb-2 flex items-center gap-2">
          <Users className="cyber-glow h-3 w-3" />
          rappel slate 7 pro
        </h3>
        <ul className="space-y-1 text-[11px] text-[color:var(--color-cyber-muted)]">
          <li>
            <span className="cyber-glow-soft font-mono">2GHz</span> → max ~688
            Mbps · portée, IoT
          </li>
          <li>
            <span className="cyber-glow-soft font-mono">5GHz</span> → max ~4.3
            Gbps · daily driver
          </li>
          <li>
            <span className="cyber-glow-soft font-mono">6GHz</span> → max ~5.7
            Gbps · clients Wi-Fi 6E/7 récents uniquement
          </li>
          <li>
            <span className="cyber-glow-soft font-mono">MLO</span> → agrégat
            2.4+5+6 GHz simultané · clients Wi-Fi 7 supportant MLO
          </li>
          <li className="mt-2 italic">
            <KeyRound className="mr-1 inline h-3 w-3" />
            8 SSIDs simultanés max sur le firmware GL.iNet 4.8.x.
          </li>
        </ul>
      </footer>
    </div>
  );
}
