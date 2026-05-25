import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  Network as NetworkIcon,
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
import { listNetworks } from "@/api/networks";
import PasswordGenerator from "@/components/PasswordGenerator";
import SsidSuggestionsPicker, {
  UniverseCombosPanel,
} from "@/components/SsidSuggestionsPicker";
import WifiQRModal from "@/components/WifiQRModal";
import type {
  WifiBand,
  WifiSecurity,
  WifiSsidPublic,
  WifiSsidWrite,
} from "@/types/wifi";
import type { NetworkPublic } from "@/types/network";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

const BANDS: WifiBand[] = ["2GHz", "5GHz", "6GHz", "MLO"];
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
  networks: NetworkPublic[];
  onClose: () => void;
}

function WifiForm({ initialSlug, initial, networks, onClose }: FormProps) {
  const isEdit = Boolean(initial);
  const [slug, setSlug] = useState(initialSlug ?? "");
  const [ssidName, setSsidName] = useState(initial?.ssid_name ?? "");
  const [band, setBand] = useState<WifiBand>(initial?.band ?? "5GHz");
  const [security, setSecurity] = useState<WifiSecurity>(
    initial?.security ?? "WPA3-SAE",
  );
  const [networkSlug, setNetworkSlug] = useState<string>(
    initial?.network_slug ?? networks[0]?.slug ?? "lan",
  );
  const [clientIsolation, setClientIsolation] = useState(
    initial?.client_isolation ?? false,
  );
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [changePassword, setChangePassword] = useState(!isEdit);

  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: () => {
      const isOpen = security === "open";
      const body: WifiSsidWrite = {
        ssid_name: ssidName,
        band,
        security,
        password: isOpen ? "" : changePassword ? password : null,
        network_slug: networkSlug,
        client_isolation: clientIsolation,
        notes,
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
        <label className="block">
          <span className="cyber-label mb-1.5 block">band</span>
          <select
            value={band}
            onChange={(e) => setBand(e.target.value as WifiBand)}
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          >
            {BANDS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
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
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">network (subnet)</span>
          <select
            value={networkSlug}
            onChange={(e) => setNetworkSlug(e.target.value)}
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          >
            {networks.map((n) => (
              <option key={n.slug} value={n.slug}>
                {n.slug} · {n.subnet_cidr}
                {n.isolated_from_lan ? " · isolé" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

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

// ---------------------------- Card ---------------------------- #

function WifiCard({
  ssid,
  networks,
  onEdit,
  onDeleted,
  onShowQR,
}: {
  ssid: WifiSsidPublic;
  networks: NetworkPublic[];
  onEdit: () => void;
  onDeleted: () => void;
  onShowQR: () => void;
}) {
  const network = networks.find((n) => n.slug === ssid.network_slug);
  const del = useMutation({
    mutationFn: () => deleteWifiSsid(ssid.slug),
    onSuccess: onDeleted,
  });

  return (
    <article className="cyber-card p-5">
      <div className="flex items-start gap-3">
        <div className="cyber-glow flex h-10 w-10 shrink-0 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10">
          <WifiIcon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="cyber-display cyber-glow text-base">{ssid.slug}</h3>
            <span className="cyber-chip">{ssid.band}</span>
            <span className="cyber-chip">{ssid.security}</span>
            {ssid.client_isolation && (
              <span className="cyber-chip cyber-chip-warn">client iso</span>
            )}
            {ssid.has_password ? (
              <span className="cyber-chip cyber-chip-on">PSK</span>
            ) : (
              <span className="cyber-chip">open</span>
            )}
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
            broadcast:{" "}
            <span className="cyber-glow-soft font-mono">{ssid.ssid_name}</span>
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[color:var(--color-cyber-muted)]">
            <NetworkIcon className="h-3 w-3" />
            <span className="font-mono">{ssid.network_slug}</span>
            {network && (
              <>
                <span>·</span>
                <span className="font-mono">{network.subnet_cidr}</span>
                {network.isolated_from_lan && (
                  <span className="cyber-glow-amber">· isolé du LAN</span>
                )}
              </>
            )}
          </p>
          {ssid.notes && (
            <p className="mt-2 text-[11px] italic text-[color:var(--color-cyber-dim)]">
              {ssid.notes}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={onShowQR}
            title="QR code WiFi"
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <QrCode className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={onEdit}
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => {
              if (confirm(`Supprimer le SSID "${ssid.slug}" ?`)) del.mutate();
            }}
            disabled={del.isPending}
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {del.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(del.error)}
        </p>
      )}
    </article>
  );
}

// ---------------------------- Page ---------------------------- #

export default function Wifi() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<WifiSsidPublic | null>(null);
  const [creating, setCreating] = useState(false);
  const [qrFor, setQrFor] = useState<WifiSsidPublic | null>(null);

  const ssids = useQuery({ queryKey: ["wifi"], queryFn: listWifiSsids });
  const networks = useQuery({ queryKey: ["networks"], queryFn: listNetworks });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["wifi"] });
  const closeForm = () => {
    setEditing(null);
    setCreating(false);
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <WifiIcon className="cyber-glow h-3 w-3" />
            wifi catalog · {ssids.data?.length ?? 0} ssid(s)
          </div>
          <h1
            className="cyber-display cyber-glitch text-4xl"
            data-text="WI-FI"
          >
            WI-FI
          </h1>
          <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            SSIDs · band · security · network (subnet) · client isolation
          </p>
        </div>
        {!creating && !editing && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs"
          >
            <Plus className="h-3.5 w-3.5" />
            Nouveau SSID
          </button>
        )}
      </header>

      {creating && networks.data && (
        <section className="mb-6">
          <WifiForm networks={networks.data} onClose={closeForm} />
        </section>
      )}

      {editing && networks.data && (
        <section className="mb-6">
          <WifiForm
            initialSlug={editing.slug}
            initial={editing}
            networks={networks.data}
            onClose={closeForm}
          />
        </section>
      )}

      {(ssids.isLoading || networks.isLoading) && (
        <p className="cyber-label cyber-cursor">chargement</p>
      )}

      {ssids.isError && (
        <div className="cyber-card cyber-card-accent p-4 text-sm text-[color:var(--color-cyber-accent)]">
          {errorMessage(ssids.error)}
        </div>
      )}

      {ssids.data && networks.data && ssids.data.length === 0 && (
        <p className="text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
          ▸ aucun SSID. Crée le premier !
        </p>
      )}

      {ssids.data && networks.data && ssids.data.length > 0 && (
        <div
          className={cn(
            "grid grid-cols-1 gap-4",
            !creating && !editing && "lg:grid-cols-2",
          )}
        >
          {ssids.data.map((s) => (
            <WifiCard
              key={s.slug}
              ssid={s}
              networks={networks.data!}
              onEdit={() => {
                setCreating(false);
                setEditing(s);
              }}
              onDeleted={refresh}
              onShowQR={() => setQrFor(s)}
            />
          ))}
        </div>
      )}

      {qrFor && (
        <WifiQRModal
          slug={qrFor.slug}
          ssidName={qrFor.ssid_name}
          onClose={() => setQrFor(null)}
        />
      )}

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
