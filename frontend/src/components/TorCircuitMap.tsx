import { useMemo, useState } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
  Line,
  Marker,
} from "react-simple-maps";

import type { TorCircuitInfo, TorRelayHop } from "@/types/tor";
import { coordsFor, flagFor } from "@/lib/country-coords";

// 110-meter world atlas — ~100 KB, country shapes only, no coastlines.
// Hosted by jsdelivr from world-atlas package.
const WORLD_TOPO_URL =
  "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";

type HopRole = "entry" | "middle" | "exit";

interface PlottedHop {
  hop: TorRelayHop;
  role: HopRole;
  coords: [number, number];
  circuitId: string;
}

function rolesFor(hops: TorRelayHop[]): HopRole[] {
  if (hops.length === 0) return [];
  if (hops.length === 1) return ["exit"];
  if (hops.length === 2) return ["entry", "exit"];
  // Standard 3-hop: entry → middle → exit. Longer paths (vanilla bridges,
  // onion services) get extra "middle" labels.
  const out: HopRole[] = ["entry"];
  for (let i = 1; i < hops.length - 1; i++) out.push("middle");
  out.push("exit");
  return out;
}

const ROLE_COLOR: Record<HopRole, string> = {
  entry: "#22d3ee",   // cyan-400
  middle: "#c084fc",  // purple-400
  exit: "#f0abfc",    // fuchsia-300
};

/**
 * Cyberpunk world map plotting active Tor circuits.
 *
 * Each circuit becomes a polyline (entry → middle → exit) with one
 * marker per hop. Coordinates come from the hop's country code looked
 * up in COUNTRY_COORDS — accurate enough to convey "this packet went
 * through Sweden then Romania then exits in Germany" without paying
 * for an IP-geolocation API.
 *
 * Hops whose country we can't resolve (geoip miss or "??") are dropped
 * from the plot rather than landing at (0,0). The header line shows
 * how many circuits made it onto the map.
 */
export default function TorCircuitMap({
  circuits,
}: {
  circuits: TorCircuitInfo[];
}) {
  const [hovered, setHovered] = useState<PlottedHop | null>(null);

  const { plottedCircuits, allHops } = useMemo(() => {
    const out: { id: string; points: PlottedHop[] }[] = [];
    const flat: PlottedHop[] = [];
    for (const c of circuits) {
      const roles = rolesFor(c.hops);
      const points: PlottedHop[] = [];
      c.hops.forEach((h, i) => {
        const cc = h.country;
        const coords = coordsFor(cc);
        if (!coords) return;
        const p: PlottedHop = {
          hop: h, role: roles[i] ?? "middle",
          coords, circuitId: c.circuit_id,
        };
        points.push(p);
        flat.push(p);
      });
      if (points.length >= 2) out.push({ id: c.circuit_id, points });
    }
    return { plottedCircuits: out, allHops: flat };
  }, [circuits]);

  if (circuits.length === 0) {
    return (
      <div className="cyber-panel border border-zinc-800 bg-zinc-950/40 p-8 text-center text-xs text-zinc-500">
        Aucun circuit actif. Active le daemon Tor et un peu de trafic le
        construit en quelques secondes.
      </div>
    );
  }

  return (
    <div className="relative cyber-panel overflow-hidden rounded border border-purple-500/30 bg-gradient-to-b from-zinc-950 to-zinc-900/80">
      <ComposableMap
        projection="geoEqualEarth"
        projectionConfig={{ scale: 165 }}
        style={{ width: "100%", height: "auto" }}
      >
        {/* Subtle grid look — countries as outlined dark shapes. */}
        <Geographies geography={WORLD_TOPO_URL}>
          {({ geographies }) =>
            geographies.map((geo) => (
              <Geography
                key={geo.rsmKey}
                geography={geo}
                style={{
                  default: {
                    fill: "#18181b",        // zinc-900
                    stroke: "#3f3f46",       // zinc-700
                    strokeWidth: 0.4,
                    outline: "none",
                  },
                  hover: { fill: "#27272a", outline: "none" },
                  pressed: { fill: "#27272a", outline: "none" },
                }}
              />
            ))
          }
        </Geographies>

        {/* One polyline per circuit, drawn entry → middle → exit. */}
        {plottedCircuits.map((c, idx) => {
          // Compose successive Line elements between consecutive hops.
          const segs: JSX.Element[] = [];
          for (let i = 0; i < c.points.length - 1; i++) {
            const a = c.points[i]!;
            const b = c.points[i + 1]!;
            const stroke = ROLE_COLOR[b.role];
            segs.push(
              <Line
                key={`${c.id}-${i}`}
                from={a.coords}
                to={b.coords}
                stroke={stroke}
                strokeWidth={1}
                strokeOpacity={0.55}
                strokeLinecap="round"
                style={{ filter: `drop-shadow(0 0 1.5px ${stroke})` }}
              />,
            );
          }
          return <g key={c.id} opacity={0.85 - idx * 0.04}>{segs}</g>;
        })}

        {/* One marker per hop. Drawn AFTER lines so dots sit on top. */}
        {allHops.map((p, i) => {
          const color = ROLE_COLOR[p.role];
          const isHover =
            hovered?.hop.fingerprint === p.hop.fingerprint &&
            hovered?.circuitId === p.circuitId;
          return (
            <Marker
              key={`${p.circuitId}-${p.hop.fingerprint}-${i}`}
              coordinates={p.coords}
              onMouseEnter={() => setHovered(p)}
              onMouseLeave={() => setHovered(null)}
            >
              <circle
                r={isHover ? 4.5 : 2.8}
                fill={color}
                stroke="#0a0a0a"
                strokeWidth={0.6}
                style={{
                  filter: `drop-shadow(0 0 3px ${color})`,
                  cursor: "pointer",
                  transition: "r 120ms ease",
                }}
              />
            </Marker>
          );
        })}
      </ComposableMap>

      {/* Legend (top-left) */}
      <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-3 text-[10px] text-zinc-300">
        <span className="cyber-chip cyber-chip-ghost px-1.5 py-0.5">
          {plottedCircuits.length}/{circuits.length} circuits
        </span>
        {(["entry", "middle", "exit"] as HopRole[]).map((r) => (
          <span key={r} className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{
                backgroundColor: ROLE_COLOR[r],
                boxShadow: `0 0 4px ${ROLE_COLOR[r]}`,
              }}
            />
            {r}
          </span>
        ))}
      </div>

      {/* Hover tooltip (bottom-right) */}
      {hovered && (
        <div className="pointer-events-none absolute bottom-3 right-3 max-w-xs rounded border border-purple-500/40 bg-zinc-950/95 px-3 py-2 text-[11px] backdrop-blur">
          <div className="mb-0.5 flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: ROLE_COLOR[hovered.role] }}
            />
            <span className="cyber-chip cyber-chip-ghost px-1.5 py-0.5 uppercase">
              {hovered.role}
            </span>
            <span className="text-zinc-200">
              {flagFor(hovered.hop.country)} {hovered.hop.country?.toUpperCase() ?? "??"}
            </span>
          </div>
          <div className="font-semibold text-purple-200">
            {hovered.hop.nickname || "unknown"}
          </div>
          {hovered.hop.ip && (
            <div className="font-mono text-[10px] text-zinc-400">
              {hovered.hop.ip}
            </div>
          )}
          {hovered.hop.bandwidth_kbps !== null && (
            <div className="text-[10px] text-zinc-400">
              bandwidth {hovered.hop.bandwidth_kbps?.toLocaleString()} kbps
            </div>
          )}
          <div className="mt-1 text-[10px] text-zinc-500">
            circuit #{hovered.circuitId}
          </div>
        </div>
      )}
    </div>
  );
}
