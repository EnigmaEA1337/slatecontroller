/**
 * DeviceLocationButton — compact « Fix location » UI embedded in a device
 * card. One tap captures the browser's GPS fix and posts it to the
 * controller as the device's current location, so the operator never
 * has to leave the Devices page to keep the geoloc up to date.
 *
 * Layout :
 *   ┌───────────────────────────────────────────────┐
 *   │ 📍  Localisation                              │
 *   │ 48.8566, 2.3522 · ±18 m · il y a 3 h          │
 *   │ [ Fix location  ◷ ]  [ Manuel… ]              │
 *   └───────────────────────────────────────────────┘
 *
 * The full history view + per-source filtering lives on Réseaux → Radio
 * (DeviceLocationPanel) ; this component is the everyday entry point.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crosshair, MapPin, Pencil, X } from "lucide-react";
import {
  addDeviceLocation,
  getDeviceLocations,
} from "@/api/device-locations";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

function fmtAge(iso: string): string {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `il y a ${Math.floor(s)} s`;
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
  return `il y a ${Math.floor(s / 86400)} j`;
}

export default function DeviceLocationButton() {
  const queryClient = useQueryClient();
  const [manualOpen, setManualOpen] = useState(false);
  const [browserError, setBrowserError] = useState<string | null>(null);

  const locations = useQuery({
    queryKey: ["device-locations"],
    queryFn: getDeviceLocations,
  });

  const add = useMutation({
    mutationFn: addDeviceLocation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["device-locations"] });
      setManualOpen(false);
    },
  });

  const captureGps = () => {
    setBrowserError(null);
    if (!navigator.geolocation) {
      setBrowserError(
        "Géolocalisation navigateur indisponible (HTTPS requis)",
      );
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        add.mutate({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy_m: pos.coords.accuracy,
          source: "browser",
          label: "",
          note: "",
        });
      },
      (err) => setBrowserError(`Erreur GPS : ${err.message}`),
      { enableHighAccuracy: true, timeout: 8_000, maximumAge: 30_000 },
    );
  };

  const current = locations.data?.current ?? null;

  return (
    <div className="mt-2">
      <div className="cyber-label mb-1 flex items-center gap-1.5">
        <MapPin className="h-3 w-3" />
        localisation
        {current && (
          <span className="ml-auto text-[9px] normal-case tracking-normal text-[color:var(--color-cyber-dim)]">
            {fmtAge(current.created_at)}
          </span>
        )}
      </div>

      {current ? (
        <p className="font-mono text-[11px] text-[color:var(--color-cyber-fg)]">
          {current.lat.toFixed(6)}, {current.lon.toFixed(6)}
          {current.accuracy_m != null && (
            <span className="ml-2 text-[10px] text-[color:var(--color-cyber-muted)]">
              ±{Math.round(current.accuracy_m)} m
            </span>
          )}
          <span className="ml-2 cyber-chip text-[9px]">{current.source}</span>
        </p>
      ) : (
        <p className="border border-dashed border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] italic text-[color:var(--color-cyber-dim)]">
          Aucune position enregistrée pour ce device.
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={add.isPending}
          onClick={captureGps}
          className="cyber-button inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] disabled:opacity-50"
          title="Capture la position du navigateur (HTTPS requis) et l'enregistre comme position courante"
        >
          <Crosshair
            className={cn(
              "h-3 w-3",
              add.isPending && "animate-pulse",
            )}
          />
          {add.isPending ? "fix…" : "Fix location"}
        </button>
        <button
          type="button"
          onClick={() => setManualOpen((v) => !v)}
          className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          <Pencil className="h-3 w-3" />
          {manualOpen ? "Annuler" : "Manuel…"}
        </button>
      </div>

      {browserError && (
        <p className="mt-2 cyber-chip cyber-chip-on block !rounded-none px-2 py-1 text-[10px]">
          {browserError}
        </p>
      )}
      {add.error && (
        <p className="mt-2 cyber-chip cyber-chip-on block !rounded-none px-2 py-1 text-[10px]">
          {errorMessage(add.error)}
        </p>
      )}

      {manualOpen && (
        <ManualLocationForm
          initialLat={current?.lat ?? null}
          initialLon={current?.lon ?? null}
          onSubmit={(p) => add.mutate(p)}
          onCancel={() => setManualOpen(false)}
          pending={add.isPending}
        />
      )}
    </div>
  );
}

function ManualLocationForm({
  initialLat,
  initialLon,
  onSubmit,
  onCancel,
  pending,
}: {
  initialLat: number | null;
  initialLon: number | null;
  onSubmit: (p: {
    lat: number;
    lon: number;
    accuracy_m: number | null;
    source: string;
    label: string;
    note: string;
  }) => void;
  onCancel: () => void;
  pending: boolean;
}) {
  const [lat, setLat] = useState(initialLat?.toString() ?? "");
  const [lon, setLon] = useState(initialLon?.toString() ?? "");
  const [label, setLabel] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const latN = parseFloat(lat);
    const lonN = parseFloat(lon);
    if (!Number.isFinite(latN) || latN < -90 || latN > 90) return;
    if (!Number.isFinite(lonN) || lonN < -180 || lonN > 180) return;
    onSubmit({
      lat: latN,
      lon: lonN,
      accuracy_m: null,
      source: "manual",
      label: label.trim(),
      note: "",
    });
  }

  return (
    <form
      onSubmit={submit}
      className="mt-2 space-y-2 border border-[color:var(--color-cyber-border)] p-2"
    >
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="cyber-label !text-[9px] mb-0.5 block">latitude</span>
          <input
            type="number"
            step="any"
            min={-90}
            max={90}
            required
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            placeholder="48.8566"
            className="cyber-input w-full px-2 py-1 text-[11px] font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label !text-[9px] mb-0.5 block">longitude</span>
          <input
            type="number"
            step="any"
            min={-180}
            max={180}
            required
            value={lon}
            onChange={(e) => setLon(e.target.value)}
            placeholder="2.3522"
            className="cyber-input w-full px-2 py-1 text-[11px] font-mono"
          />
        </label>
      </div>
      <label className="block">
        <span className="cyber-label !text-[9px] mb-0.5 block">
          label (optionnel)
        </span>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="ex. domicile, bureau, hôtel Paris…"
          className="cyber-input w-full px-2 py-1 text-[11px]"
        />
      </label>
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending || !lat || !lon}
          className="cyber-button px-3 py-1 text-[10px] disabled:opacity-50"
        >
          {pending ? "save…" : "Enregistrer"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="border border-[color:var(--color-cyber-border-strong)] px-3 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    </form>
  );
}
