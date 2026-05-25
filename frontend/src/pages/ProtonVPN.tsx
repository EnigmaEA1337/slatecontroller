import { FormEvent, memo, useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  FileUp,
  Network,
  Server,
  ShieldCheck,
  Trash2,
  Upload,
} from "lucide-react";
import {
  deleteVPNConfig,
  listVPNConfigs,
  uploadVPNConfig,
} from "@/api/vpn";
import type { VPNConfigPublic } from "@/types/vpn";
import { errorMessage } from "@/lib/error-utils";


function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("aucun fichier sélectionné");
      return uploadVPNConfig(file, name || file.name.replace(/\.conf$/, ""));
    },
    onSuccess: () => {
      setFile(null);
      setName("");
      onUploaded();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate();
  }

  return (
    <form onSubmit={onSubmit} className="cyber-card cyber-card-accent p-6">
      <h2 className="cyber-label mb-4 flex items-center gap-2">
        <Upload className="cyber-glow h-3 w-3" />
        upload config wireguard
      </h2>

      <div className="space-y-4">
        <label className="block cursor-pointer">
          <span className="cyber-label mb-2 block">fichier .conf</span>
          <div className="cyber-input flex items-center gap-3 px-3 py-3 text-xs">
            <FileUp className="h-4 w-4 text-[color:var(--color-cyber-accent)]" />
            <span className="flex-1 truncate font-mono">
              {file?.name ?? "Cliquer pour choisir…"}
            </span>
            <input
              type="file"
              accept=".conf,text/plain,application/x-wireguard-config"
              required
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f && !name) {
                  setName(f.name.replace(/\.conf$/i, ""));
                }
              }}
            />
          </div>
        </label>

        <label className="block">
          <span className="cyber-label mb-2 block">
            nom du config (slug)
          </span>
          <input
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="cyber-input w-full py-2.5 px-3 text-sm font-mono"
            placeholder="proton-fr-12"
          />
        </label>

        {mutation.error && (
          <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
            {errorMessage(mutation.error)}
          </p>
        )}

        <button
          type="submit"
          disabled={!file || mutation.isPending}
          className="cyber-button w-full px-4 py-3 text-sm"
        >
          {mutation.isPending ? "// uploading…" : "Stocker ▸"}
        </button>
      </div>
    </form>
  );
}

// Memoised because it's rendered in a .map() over every VPN config — keeps
// the row stable when the parent re-renders (e.g. polling refresh, unrelated
// state change). Default shallow compare on `config` + `onDeleted` works
// since both are stable references from the parent's perspective.
const ConfigRow = memo(function ConfigRow({
  config,
  onDeleted,
}: {
  config: VPNConfigPublic;
  onDeleted: () => void;
}) {
  const mutation = useMutation({
    mutationFn: () => deleteVPNConfig(config.name),
    onSuccess: onDeleted,
  });

  const endpointHost = config.peer_endpoint.split(":")[0];

  return (
    <article className="cyber-card p-4">
      <div className="flex items-start gap-3">
        <div className="cyber-glow flex h-8 w-8 shrink-0 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10">
          <Server className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <h3 className="cyber-glow text-sm font-extrabold uppercase tracking-[0.12em]">
              {config.name}
            </h3>
            <span className="cyber-chip">{config.provider}</span>
          </div>
          <div className="mt-1 grid grid-cols-1 gap-x-4 gap-y-0.5 text-[11px] text-[color:var(--color-cyber-muted)] sm:grid-cols-2">
            <span className="truncate">
              endpoint{" "}
              <span className="cyber-glow-soft font-mono">{endpointHost}</span>
            </span>
            <span className="truncate">
              tunnel{" "}
              <span className="cyber-glow-soft font-mono">
                {config.interface_address}
              </span>
            </span>
            <span className="truncate">
              dns{" "}
              <span className="cyber-glow-soft font-mono">
                {config.dns_servers.join(", ") || "—"}
              </span>
            </span>
            <span className="truncate">
              uploaded{" "}
              <span className="font-mono">
                {new Date(config.created_at).toLocaleDateString()}
              </span>
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => {
            if (confirm(`Supprimer la config "${config.name}" ?`)) {
              mutation.mutate();
            }
          }}
          disabled={mutation.isPending}
          className="shrink-0 border border-transparent p-2 text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
          title="Supprimer"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </article>
  );
});

export default function ProtonVPN() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery<VPNConfigPublic[]>({
    queryKey: ["vpn-configs"],
    queryFn: listVPNConfigs,
  });

  // useCallback keeps `refresh` stable across renders so memoised children
  // (ConfigRow, UploadCard) don't re-render on every parent update.
  const refresh = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["vpn-configs"] }),
    [queryClient],
  );

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Network className="cyber-glow h-3 w-3" />
          vpn / wireguard / proton
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text="PROTON VPN"
        >
          PROTON VPN
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          Upload manuel des configs WireGuard ·{" "}
          <a
            href="https://account.proton.me/u/0/vpn/WireGuard"
            target="_blank"
            rel="noreferrer"
            className="cyber-glow inline-flex items-center gap-1 underline-offset-4 hover:underline"
          >
            portail Proton <ExternalLink className="h-3 w-3" />
          </a>
        </p>
      </header>

      <section className="mb-8">
        <UploadCard onUploaded={refresh} />
      </section>

      <section>
        <h2 className="cyber-label mb-4 flex items-center gap-2">
          <ShieldCheck className="cyber-glow h-3 w-3" />
          configs stockées ({data?.length ?? 0})
        </h2>

        {isLoading && <p className="cyber-label cyber-cursor">chargement</p>}

        {isError && (
          <div className="cyber-card cyber-card-accent p-4 text-sm text-[color:var(--color-cyber-accent)]">
            {errorMessage(error, "Erreur de chargement")}
          </div>
        )}

        {data && data.length === 0 && (
          <p className="text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
            ▸ Aucune config stockée pour l'instant.
          </p>
        )}

        {data && data.length > 0 && (
          <div className="space-y-3">
            {data.map((cfg) => (
              <ConfigRow key={cfg.name} config={cfg} onDeleted={refresh} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
