/**
 * DeviceLocationPanel — manage the active device's location history.
 *
 * Shown in the Radio page header so the operator can :
 *   - see where the device currently "is" (= latest entry)
 *   - add a new pin (manual coords, or capture from browser GPS)
 *   - browse history of past pins
 *   - delete a wrong entry
 *
 * The most recent entry is what gets stamped on every new scan
 * (unless the operator explicitly overrides at scan-time, but that's
 * a future feature).
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Crosshair,
  MapPin,
  Plus,
  Smartphone,
  Trash2,
  X,
} from "lucide-react";
import {
  addDeviceLocation,
  deleteDeviceLocation,
  getDeviceLocations,
  type DeviceLocationView,
} from "@/api/device-locations";
import { errorMessage } from "@/lib/error-utils";
import { cn } from "@/lib/utils";

export default function DeviceLocationPanel() {
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);

  const list = useQuery({
    queryKey: ["device-locations"],
    queryFn: getDeviceLocations,
  });

  const addMut = useMutation({
    mutationFn: addDeviceLocation,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["device-locations"] });
      setShowAdd(false);
    },
  });

  const delMut = useMutation({
    mutationFn: deleteDeviceLocation,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["device-locations"] }),
  });

  return (
    <section className="cyber-card p-4 space-y-3">
      <header className="flex items-center justify-between">
        <div className="cyber-label text-[10px] flex items-center gap-2">
          <MapPin className="h-3 w-3" /> position du device
        </div>
        <button
          onClick={() => setShowAdd((s) => !s)}
          className="cyber-button-ghost px-2 py-1 text-[10px]"
        >
          {showAdd ? (
            <>
              <X className="inline h-3 w-3 mr-1" /> annuler
            </>
          ) : (
            <>
              <Plus className="inline h-3 w-3 mr-1" /> ajouter
            </>
          )}
        </button>
      </header>

      {list.isError && (
        <div className="cyber-chip cyber-chip-on px-3 py-2 text-xs">
          {errorMessage(list.error)}
        </div>
      )}

      {list.data && (
        <CurrentLocation
          current={list.data.current}
          onDelete={(id) => delMut.mutate(id)}
          deleteDisabled={delMut.isPending}
        />
      )}

      {showAdd && (
        <AddLocationForm
          onSubmit={(p) => addMut.mutate(p)}
          submitting={addMut.isPending}
          submitError={addMut.error ? errorMessage(addMut.error) : null}
        />
      )}

      {list.data && list.data.history.length > 1 && (
        <details className="text-xs">
          <summary className="cyber-label text-[10px] cursor-pointer">
            historique ({list.data.history.length - 1} précédente
            {list.data.history.length - 1 > 1 ? "s" : ""})
          </summary>
          <div className="mt-2 space-y-1">
            {list.data.history.slice(1).map((h) => (
              <HistoryRow
                key={h.id}
                entry={h}
                onDelete={() => delMut.mutate(h.id)}
                deleteDisabled={delMut.isPending}
              />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function CurrentLocation({
  current,
  onDelete,
  deleteDisabled,
}: {
  current: DeviceLocationView | null;
  onDelete: (id: number) => void;
  deleteDisabled: boolean;
}) {
  if (!current) {
    return (
      <p className="text-xs text-[color:var(--color-cyber-muted)]">
        // aucune position enregistrée. Click "ajouter" pour épingler une
        position — manuelle ou via GPS du navigateur.
      </p>
    );
  }
  return (
    <div className="flex items-start gap-3">
      <div className="flex-1 space-y-1">
        <div className="font-mono text-sm text-[color:var(--color-cyber-accent)] cyber-glow">
          {current.lat.toFixed(6)}, {current.lon.toFixed(6)}
        </div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
          <SourceBadge source={current.source} />{" "}
          {current.label && (
            <span className="cyber-chip ml-1 text-[9px]">{current.label}</span>
          )}
          {current.accuracy_m != null && (
            <span className="ml-2">±{Math.round(current.accuracy_m)}m</span>
          )}
          <span className="ml-2">
            {new Date(current.created_at).toLocaleString("fr-FR")}
          </span>
        </div>
        {current.note && (
          <p className="text-[10px] italic text-[color:var(--color-cyber-muted)]">
            {current.note}
          </p>
        )}
      </div>
      <button
        onClick={() => onDelete(current.id)}
        disabled={deleteDisabled}
        title="Supprimer cette position"
        className="cyber-button-ghost p-1.5 text-[10px]"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}

function SourceBadge({ source }: { source: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    manual: {
      label: "📌 manuel",
      cls: "bg-[color:var(--color-cyber-bg-2)] text-[color:var(--color-cyber-muted)]",
    },
    browser: {
      label: "📱 browser",
      cls: "bg-cyan-500/10 text-cyan-300",
    },
    gps_slate: {
      label: "🛰 slate",
      cls: "bg-emerald-500/10 text-emerald-300",
    },
    wardrive: {
      label: "🚗 wardrive",
      cls: "bg-amber-500/10 text-amber-300",
    },
  };
  const m = map[source] ?? {
    label: source || "?",
    cls: "bg-[color:var(--color-cyber-bg-2)]",
  };
  return (
    <span
      className={cn(
        "inline-block px-1.5 py-0.5 text-[9px] font-mono rounded",
        m.cls,
      )}
    >
      {m.label}
    </span>
  );
}

function HistoryRow({
  entry,
  onDelete,
  deleteDisabled,
}: {
  entry: DeviceLocationView;
  onDelete: () => void;
  deleteDisabled: boolean;
}) {
  return (
    <div className="flex items-center gap-2 text-[10px]">
      <SourceBadge source={entry.source} />
      <span className="font-mono">
        {entry.lat.toFixed(4)}, {entry.lon.toFixed(4)}
      </span>
      {entry.label && (
        <span className="cyber-chip text-[9px]">{entry.label}</span>
      )}
      <span className="ml-auto text-[color:var(--color-cyber-muted)]">
        {new Date(entry.created_at).toLocaleString("fr-FR", {
          dateStyle: "short",
          timeStyle: "short",
        })}
      </span>
      <button
        onClick={onDelete}
        disabled={deleteDisabled}
        className="cyber-button-ghost p-1 text-[9px]"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}

function AddLocationForm({
  onSubmit,
  submitting,
  submitError,
}: {
  onSubmit: (p: {
    lat: number;
    lon: number;
    accuracy_m?: number | null;
    source?: string;
    label?: string;
    note?: string;
  }) => void;
  submitting: boolean;
  submitError: string | null;
}) {
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [source, setSource] = useState<"manual" | "browser">("manual");
  const [browserAcc, setBrowserAcc] = useState<number | null>(null);
  const [browserError, setBrowserError] = useState<string | null>(null);

  // Try slate-GPS first when the panel opens (best-effort, fails silent
  // when no dongle / gpsd).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch("/api/wifi/gps/current", {
          credentials: "include",
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (cancelled) return;
        if (typeof data.lat === "number" && typeof data.lon === "number") {
          setLat(String(data.lat));
          setLon(String(data.lon));
          setSource("manual"); // we'll relabel as gps_slate at submit
          setBrowserAcc(
            typeof data.accuracy_m === "number" ? data.accuracy_m : null,
          );
        }
      } catch {
        /* silent */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const captureBrowser = () => {
    setBrowserError(null);
    if (!navigator.geolocation) {
      setBrowserError("Browser geolocation indisponible (HTTPS requis)");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(String(pos.coords.latitude));
        setLon(String(pos.coords.longitude));
        setBrowserAcc(pos.coords.accuracy);
        setSource("browser");
      },
      (err) => setBrowserError(`Erreur GPS browser : ${err.message}`),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30_000 },
    );
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const latN = parseFloat(lat);
    const lonN = parseFloat(lon);
    if (!Number.isFinite(latN) || latN < -90 || latN > 90) return;
    if (!Number.isFinite(lonN) || lonN < -180 || lonN > 180) return;
    onSubmit({
      lat: latN,
      lon: lonN,
      accuracy_m: source === "browser" ? browserAcc : null,
      source: source,
      label: label.trim(),
      note: note.trim(),
    });
  };

  return (
    <form onSubmit={submit} className="space-y-3 border-t border-[color:var(--color-cyber-border)] pt-3">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="cyber-label text-[9px] mb-1">latitude</div>
          <input
            type="number"
            step="any"
            min={-90}
            max={90}
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            placeholder="48.8566"
            className="cyber-input w-full text-sm font-mono"
            required
          />
        </div>
        <div>
          <div className="cyber-label text-[9px] mb-1">longitude</div>
          <input
            type="number"
            step="any"
            min={-180}
            max={180}
            value={lon}
            onChange={(e) => setLon(e.target.value)}
            placeholder="2.3522"
            className="cyber-input w-full text-sm font-mono"
            required
          />
        </div>
      </div>

      <div>
        <div className="cyber-label text-[9px] mb-1">label (optionnel)</div>
        <input
          type="text"
          maxLength={64}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Bureau / Mission / Maison …"
          className="cyber-input w-full text-sm"
        />
      </div>

      <div>
        <div className="cyber-label text-[9px] mb-1">note (optionnel)</div>
        <input
          type="text"
          maxLength={256}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="contexte, repère, etc."
          className="cyber-input w-full text-sm"
        />
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <button
          type="button"
          onClick={captureBrowser}
          disabled={submitting}
          className="cyber-button-ghost px-3 py-1.5 text-xs"
        >
          <Smartphone className="inline h-3 w-3 mr-1" /> Browser GPS
        </button>
        <button
          type="button"
          onClick={() => {
            fetch("/api/wifi/gps/current", { credentials: "include" })
              .then((r) => (r.ok ? r.json() : null))
              .then((d) => {
                if (!d) return;
                if (typeof d.lat === "number") setLat(String(d.lat));
                if (typeof d.lon === "number") setLon(String(d.lon));
                if (typeof d.accuracy_m === "number") setBrowserAcc(d.accuracy_m);
              })
              .catch(() => {});
          }}
          disabled={submitting}
          className="cyber-button-ghost px-3 py-1.5 text-xs"
          title="GPS du dongle USB sur le Slate (si présent)"
        >
          <Crosshair className="inline h-3 w-3 mr-1" /> Slate GPS
        </button>
        {browserAcc != null && (
          <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
            ±{Math.round(browserAcc)}m
          </span>
        )}
      </div>

      {browserError && (
        <p className="text-xs text-red-300">{browserError}</p>
      )}
      {submitError && (
        <p className="text-xs text-red-300">{submitError}</p>
      )}

      <button
        type="submit"
        disabled={submitting || !lat || !lon}
        className="cyber-button w-full px-4 py-2 text-sm"
      >
        {submitting ? "// enregistrement…" : "Épingler position"}
      </button>
    </form>
  );
}
