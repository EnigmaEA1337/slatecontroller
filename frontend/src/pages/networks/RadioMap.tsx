/**
 * RÉSEAU → Radio · Carte
 *
 * Map view of every geolocated artefact for the active device :
 *   - the device's CURRENT location (most-recent device_locations entry)
 *     rendered with a prominent cyber-accent marker
 *   - every PAST device location as a smaller dimmed marker → operator
 *     can see the deployment history at a glance
 *   - every SCAN that has lat/lon, color-coded by band
 *
 * Implementation : Leaflet with OpenStreetMap tiles (no API key).
 * Tile attribution stays visible per the OSM ODbL.
 *
 * Markers use SVG icons inlined as data URLs so we don't depend on the
 * default Leaflet marker PNGs (which break under Vite's import-asset
 * pipeline without a manual delete-icon-url shim).
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  MapContainer,
  Marker,
  Popup,
  TileLayer,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { getDeviceLocations } from "@/api/device-locations";
import { listScanHistory } from "@/api/scan-history";
import { errorMessage } from "@/lib/error-utils";
import { useT } from "@/lib/i18n";
import type { WifiBand } from "@/types/wifi";

// SVG marker factory — colour-and-size-configurable so we can render
// per-band scans + device pins with the same primitive.
function makeIcon(
  color: string,
  size: number,
  label: string = "",
): L.DivIcon {
  const html = `
    <div style="
      width: ${size}px; height: ${size}px;
      border-radius: 50%;
      background: ${color};
      box-shadow: 0 0 0 2px white, 0 0 8px ${color};
      display: flex; align-items: center; justify-content: center;
      color: white; font-family: monospace; font-weight: bold;
      font-size: ${Math.max(8, size * 0.5)}px;
    ">${label}</div>
  `;
  return L.divIcon({
    html,
    className: "",
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

const ICON_CURRENT = makeIcon("#ff3a52", 24, "◉");
const ICON_PAST = makeIcon("#7a7d96", 14, "");
const ICON_BAND: Record<WifiBand, L.DivIcon> = {
  "2": makeIcon("#5ae8a8", 16, "2"),
  "5": makeIcon("#4d8fff", 16, "5"),
  "6": makeIcon("#ffb547", 16, "6"),
};

interface MapPoint {
  lat: number;
  lon: number;
}

function FitToMarkers({ points }: { points: MapPoint[] }) {
  const map = useMap();
  useMemo(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView([points[0]!.lat, points[0]!.lon], 14);
      return;
    }
    const bounds = L.latLngBounds(points.map((p) => [p.lat, p.lon]));
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
  }, [map, points]);
  return null;
}

export default function RadioMap() {
  const t = useT();
  const locations = useQuery({
    queryKey: ["device-locations"],
    queryFn: getDeviceLocations,
  });
  const scans = useQuery({
    queryKey: ["scan-history", "for-map"],
    queryFn: () => listScanHistory({ limit: 200 }),
  });

  const allPoints: MapPoint[] = useMemo(() => {
    const pts: MapPoint[] = [];
    if (locations.data) {
      for (const h of locations.data.history) {
        pts.push({ lat: h.lat, lon: h.lon });
      }
    }
    if (scans.data) {
      for (const s of scans.data) {
        if (s.lat != null && s.lon != null) {
          pts.push({ lat: s.lat, lon: s.lon });
        }
      }
    }
    return pts;
  }, [locations.data, scans.data]);

  const fallbackCenter: [number, number] = [48.8566, 2.3522]; // Paris
  const center: [number, number] =
    allPoints.length > 0
      ? [allPoints[0]!.lat, allPoints[0]!.lon]
      : fallbackCenter;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="cyber-display cyber-glow text-2xl">
          {t("net_radio_map.title").toUpperCase()}
        </h1>
        <p className="cyber-label text-[10px] mt-1">
          {t("net_radio_map.subtitle")}
        </p>
      </header>

      {locations.isError && (
        <div className="cyber-chip cyber-chip-on px-3 py-2 text-xs">
          {errorMessage(locations.error)}
        </div>
      )}

      <section className="cyber-card p-4 space-y-3">
        <div className="flex flex-wrap gap-3 text-xs">
          <LegendItem color="#ff3a52" label="Position courante" />
          <LegendItem color="#7a7d96" label="Position historique" />
          <LegendItem color="#5ae8a8" label="Scan 2.4 GHz" />
          <LegendItem color="#4d8fff" label="Scan 5 GHz" />
          <LegendItem color="#ffb547" label="Scan 6 GHz" />
        </div>

        {allPoints.length === 0 ? (
          <div className="text-xs text-[color:var(--color-cyber-muted)] py-8 text-center">
            // aucune position épinglée et aucun scan géolocalisé.
            <br />
            Va sur <span className="font-mono">Réseau → Radio · RF</span>{" "}
            pour épingler la position du device, puis lance un scan.
          </div>
        ) : (
          <div
            className="overflow-hidden rounded border border-[color:var(--color-cyber-border)]"
            style={{ height: "70vh" }}
          >
            <MapContainer
              center={center}
              zoom={14}
              style={{ height: "100%", width: "100%" }}
            >
              <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              />
              <FitToMarkers points={allPoints} />
              {locations.data?.current && (
                <Marker
                  position={[
                    locations.data.current.lat,
                    locations.data.current.lon,
                  ]}
                  icon={ICON_CURRENT}
                >
                  <Popup>
                    <div className="text-xs font-mono space-y-1">
                      <div>
                        <strong>📌 Position courante</strong>
                      </div>
                      <div>
                        {locations.data.current.lat.toFixed(6)},{" "}
                        {locations.data.current.lon.toFixed(6)}
                      </div>
                      {locations.data.current.label && (
                        <div>label: {locations.data.current.label}</div>
                      )}
                      <div>source: {locations.data.current.source}</div>
                      <div>
                        {new Date(
                          locations.data.current.created_at,
                        ).toLocaleString("fr-FR")}
                      </div>
                    </div>
                  </Popup>
                </Marker>
              )}
              {locations.data?.history.slice(1).map((h) => (
                <Marker
                  key={`loc-${h.id}`}
                  position={[h.lat, h.lon]}
                  icon={ICON_PAST}
                >
                  <Popup>
                    <div className="text-xs font-mono space-y-1">
                      <div>📌 Position historique</div>
                      <div>
                        {h.lat.toFixed(6)}, {h.lon.toFixed(6)}
                      </div>
                      {h.label && <div>label: {h.label}</div>}
                      <div>source: {h.source}</div>
                      <div>
                        {new Date(h.created_at).toLocaleString("fr-FR")}
                      </div>
                    </div>
                  </Popup>
                </Marker>
              ))}
              {scans.data?.map((s) =>
                s.lat != null && s.lon != null ? (
                  <Marker
                    key={`scan-${s.id}`}
                    position={[s.lat, s.lon]}
                    icon={ICON_BAND[s.band] ?? ICON_PAST}
                  >
                    <Popup>
                      <div className="text-xs font-mono space-y-1">
                        <div>
                          <strong>⚡ Scan {s.band} GHz</strong>
                        </div>
                        <div>
                          {s.neighbors_count} AP voisins
                          {s.threats_count > 0 && (
                            <span style={{ color: "#ffb547" }}>
                              {" "}
                              · ⚠ {s.threats_count}
                            </span>
                          )}
                        </div>
                        <div>
                          ch actuel {s.current_channel ?? "?"} · recommandé{" "}
                          {s.recommended_channel ?? "?"}
                        </div>
                        <div>
                          {new Date(s.started_at).toLocaleString("fr-FR")}
                        </div>
                      </div>
                    </Popup>
                  </Marker>
                ) : null,
              )}
            </MapContainer>
          </div>
        )}
      </section>
    </div>
  );
}

function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span
        className="inline-block h-3 w-3 rounded-full"
        style={{ background: color, boxShadow: `0 0 6px ${color}` }}
      />
      <span className="text-[color:var(--color-cyber-muted)]">{label}</span>
    </span>
  );
}
