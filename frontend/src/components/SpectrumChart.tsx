// Spectrum chart — band-by-band visualisation à la WiFi Explorer Pro.
//
// What changed (2026-06-23 rework) :
//
//  - Dedupe by (ap_root, channel) instead of one trapezoid per VAP.
//    A 6-VAP Ruckus that puts all six BSSIDs on the same channel used
//    to draw six identical superposed trapezoids — visual noise that
//    obscured the rest of the band. We now collapse to ONE trapezoid
//    per (physical-box, channel) pair, picking the strongest member
//    as the representative (loudest is the closest match to what the
//    operator's antenna actually sees).
//  - Channel markers : a vertical line on ``currentChannel`` (where
//    we broadcast) and on ``recommendedChannel`` (where the scorer
//    wants us to go). Operator reads "should I move ?" at a glance.
//  - DFS zone shading on 5 GHz (channels 52–144) so the operator
//    knows which slots can be radar-evicted. PSC slots on 6 GHz are
//    marked with a discreet "PSC" badge below the axis.
//  - Hover tooltip on each trapezoid : vendor + SSID count + RSSI.
//
// Input shape is unchanged : the ``NeighborAPView`` list straight
// from the scanner. Bandwidth still comes from ``ht_mode`` — 20 MHz
// default when unparseable so the AP doesn't disappear.

import { useMemo, useState } from "react";

import type { NeighborAPView } from "@/types/wifi-radio";
import type { WifiBand } from "@/types/wifi";

interface Props {
  neighbors: NeighborAPView[];
  band: WifiBand;
  /** Optional height override — chart scales horizontally to its container. */
  height?: number;
  /** Channel we currently broadcast on. Drawn as a solid vertical line. */
  currentChannel?: number;
  /** Channel the scorer recommends. Drawn as a dashed vertical line. */
  recommendedChannel?: number;
}

// ---------- band geometry ----------

interface BandLayout {
  /** Channel numbers we lay out on the X axis (left-to-right). */
  channels: number[];
  /** How many MHz one channel-number step represents on this band's grid. */
  mhzPerStep: number;
  /** Pretty label printed below the axis. */
  label: string;
  /** Channels in this band that are DFS (radar-restricted). */
  dfsChannels?: Set<number>;
  /** Preferred Scanning Channels (6 GHz client discovery). */
  pscChannels?: Set<number>;
}

const BAND_LAYOUT: Record<WifiBand, BandLayout> = {
  // 2.4 GHz : channels 1-13, every numbered channel is 5 MHz apart on the
  // grid. A 20 MHz AP on ch 6 paints from ch 4 to ch 8 (overlapping its
  // neighbours, which is exactly the point of showing the spectrum).
  "2": {
    channels: Array.from({ length: 13 }, (_, i) => i + 1),
    mhzPerStep: 5,
    label: "2.4 GHz · 2412 → 2472 MHz",
  },
  // 5 GHz : numbered channels are 4 apart (so 20 MHz of width = 1
  // numbered-channel step). We include the full UNII span — DFS channels
  // (52-144) show up too, useful to spot UniFi/Aruba enterprise APs.
  "5": {
    channels: [
      36, 40, 44, 48, 52, 56, 60, 64,
      100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
      149, 153, 157, 161, 165,
    ],
    mhzPerStep: 20,
    label: "5 GHz · 5180 → 5825 MHz",
    dfsChannels: new Set([
      52, 56, 60, 64,
      100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
    ]),
  },
  // 6 GHz : channels 1, 5, 9, …, 233 — every 20 MHz. We sample a
  // representative subset (channels with broad client compatibility
  // including PSC). Wi-Fi 7 320 MHz channels span 16 numbered-channel
  // steps so they cover almost the whole UNII-5/6/7/8 here.
  "6": {
    channels: Array.from({ length: 59 }, (_, i) => 1 + i * 4),
    mhzPerStep: 20,
    label: "6 GHz · 5945 → 7115 MHz",
    pscChannels: new Set([
      5, 21, 37, 53, 69, 85, 101, 117, 133, 149, 165, 181, 197, 213, 229,
    ]),
  },
};

// ---------- helpers ----------

/** Best-effort bandwidth-in-MHz from the ``ht_mode`` string. Falls back
 *  to 20 MHz when the value is unknown / "legacy" / empty. */
function widthFromHtMode(htMode: string): number {
  const upper = htMode.toUpperCase();
  if (upper.includes("320")) return 320;
  if (upper.includes("160")) return 160;
  if (upper.includes("80")) return 80;
  if (upper.includes("40")) return 40;
  return 20;
}

/** Deterministic colour from a stable identifier (ap_root or bssid).
 *  Mirrors the palette used by the tree-view group badges so the eye
 *  can match a trapezoid back to its row. */
function colorFromKey(key: string): string {
  let h = 0;
  for (let i = 0; i < key.length; i++) {
    h = (h * 31 + key.charCodeAt(i)) | 0;
  }
  const hue = ((h % 360) + 360) % 360;
  return `hsl(${hue}, 70%, 55%)`;
}

/** Map RSSI [-100, -10] dBm to [0, 1] height fraction. */
function rssiToHeightFraction(rssi: number): number {
  const clamped = Math.max(-100, Math.min(-10, rssi));
  return (clamped - -100) / (-10 - -100);
}

// ---------- aggregation : 1 trapezoid per (ap_root, channel) ----------

interface AggregatedAP {
  /** Key for React + tooltip identity. */
  key: string;
  /** Cluster id — drives the colour. */
  apRoot: string;
  /** Channel this trapezoid sits on. */
  channel: number;
  /** Representative RSSI = the loudest member of the (ap_root, channel)
   *  pair. The operator's antenna sees the loudest, so that's the
   *  signal that matters for "what's this band sound like". */
  rssiDbm: number;
  /** Widest ht_mode observed in the pair (320 wins over 160 wins over …).
   *  Real-world APs sometimes advertise different ht_modes on different
   *  VAPs of the same radio ; the widest is the one that paints the
   *  worst-case spectrum footprint. */
  widthMhz: number;
  /** Display strings. */
  ssid: string;
  vendor: string;
  /** How many VAPs the (ap_root, channel) pair contains — shown in the
   *  tooltip so the operator knows "this Ruckus has 6 SSIDs on this
   *  channel" without expanding the tree. */
  vapCount: number;
}

function aggregateForSpectrum(
  neighbors: NeighborAPView[],
): AggregatedAP[] {
  const buckets = new Map<string, AggregatedAP>();
  for (const n of neighbors) {
    if (n.channel <= 0) continue;
    const apRoot = n.ap_root || n.bssid;
    const key = `${apRoot}@${n.channel}`;
    const widthMhz = widthFromHtMode(n.ht_mode);
    const prev = buckets.get(key);
    if (prev === undefined) {
      buckets.set(key, {
        key,
        apRoot,
        channel: n.channel,
        rssiDbm: n.rssi_dbm,
        widthMhz,
        ssid: n.ssid || "<hidden>",
        vendor: n.vendor || "",
        vapCount: 1,
      });
      continue;
    }
    prev.vapCount += 1;
    if (widthMhz > prev.widthMhz) prev.widthMhz = widthMhz;
    // Keep the loudest member as the representative ; its SSID + vendor
    // anchor the tooltip text.
    if (n.rssi_dbm > prev.rssiDbm) {
      prev.rssiDbm = n.rssi_dbm;
      if (n.ssid) prev.ssid = n.ssid;
      if (n.vendor) prev.vendor = n.vendor;
    }
  }
  return [...buckets.values()];
}

// ---------- component ----------

export default function SpectrumChart({
  neighbors,
  band,
  height = 220,
  currentChannel,
  recommendedChannel,
}: Props) {
  const layout = BAND_LAYOUT[band];
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  // Filter to the band first ; ignore the rest (the tree handles cross-band).
  const items = useMemo(
    () => aggregateForSpectrum(neighbors.filter((n) => n.band === band)),
    [neighbors, band],
  );

  // Geometry : viewBox is fixed (1000 wide × `height` tall) ; the SVG
  // scales to its container via CSS. Each channel-number step = the
  // chart width divided by (#channels - 1).
  const VB_W = 1000;
  const VB_H = height;
  const PADDING_L = 36; // dBm axis label space
  const PADDING_R = 12;
  const PADDING_T = 18;
  const PADDING_B = 28; // channel labels
  const PLOT_W = VB_W - PADDING_L - PADDING_R;
  const PLOT_H = VB_H - PADDING_T - PADDING_B;

  const channelMin = layout.channels[0]!;
  const channelMax = layout.channels[layout.channels.length - 1]!;
  const channelSpan = channelMax - channelMin;

  /** Channel number → X coordinate in the viewBox. */
  const xForChannel = (ch: number) => {
    const t = (ch - channelMin) / channelSpan;
    return PADDING_L + Math.max(0, Math.min(1, t)) * PLOT_W;
  };
  /** Map dBm to Y. -10 = top, -100 = bottom. */
  const yForRssi = (rssi: number) =>
    PADDING_T + (1 - rssiToHeightFraction(rssi)) * PLOT_H;

  // Sort weakest-first so strong APs paint over weak ones — same depth
  // ordering as WiFi Explorer.
  const sorted = useMemo(
    () => [...items].sort((a, b) => a.rssiDbm - b.rssiDbm),
    [items],
  );

  // Active hover row : pulled out so we can show its details in a card
  // outside the SVG (SVG <title> tooltips are slow + ugly).
  const hovered = useMemo(
    () => (hoverKey ? items.find((n) => n.key === hoverKey) : null),
    [items, hoverKey],
  );

  if (sorted.length === 0) {
    return (
      <div className="cyber-card p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Aucun AP {band} GHz à afficher dans le spectre.
      </div>
    );
  }

  // Pre-compute DFS shading rectangles (5 GHz only) as contiguous spans.
  // We coalesce adjacent DFS channels into one rectangle to avoid 16
  // tiny stripes (52, 56, 60, 64 → one 52-64 span).
  const dfsSpans: Array<[number, number]> = [];
  if (layout.dfsChannels) {
    const sortedDfs = [...layout.dfsChannels].sort((a, b) => a - b);
    let runStart = sortedDfs[0]!;
    let runEnd = sortedDfs[0]!;
    for (let i = 1; i < sortedDfs.length; i++) {
      const ch = sortedDfs[i]!;
      if (ch - runEnd <= 4) {
        runEnd = ch;
      } else {
        dfsSpans.push([runStart, runEnd]);
        runStart = ch;
        runEnd = ch;
      }
    }
    dfsSpans.push([runStart, runEnd]);
  }

  return (
    <div className="cyber-card p-3 relative">
      <header className="cyber-label text-[10px] mb-2 flex items-center justify-between gap-2 flex-wrap">
        <span>spectre · {layout.label}</span>
        <span className="text-[color:var(--color-cyber-muted)] flex items-center gap-3">
          {currentChannel !== undefined && (
            <span className="flex items-center gap-1">
              <span
                className="inline-block w-3 h-0.5"
                style={{ background: "var(--color-cyber-accent)" }}
              />
              actuel ch {currentChannel}
            </span>
          )}
          {recommendedChannel !== undefined &&
            recommendedChannel !== currentChannel && (
              <span className="flex items-center gap-1">
                <span
                  className="inline-block w-3 h-0.5"
                  style={{
                    background: "#fbbf24",
                    backgroundImage:
                      "repeating-linear-gradient(to right, #fbbf24 0 4px, transparent 4px 6px)",
                  }}
                />
                conseillé ch {recommendedChannel}
              </span>
            )}
          {dfsSpans.length > 0 && (
            <span className="flex items-center gap-1">
              <span
                className="inline-block w-3 h-2"
                style={{ background: "rgba(251, 191, 36, 0.12)" }}
              />
              DFS
            </span>
          )}
          <span>{sorted.length} cibles</span>
        </span>
      </header>

      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        className="w-full block"
        style={{ background: "var(--color-cyber-bg-2)" }}
        preserveAspectRatio="none"
        onMouseLeave={() => setHoverKey(null)}
      >
        {/* DFS zone shading (5 GHz). Painted under the gridlines + APs. */}
        {dfsSpans.map(([from, to]) => {
          const xFrom = xForChannel(from - 2);
          const xTo = xForChannel(to + 2);
          return (
            <rect
              key={`dfs-${from}-${to}`}
              x={xFrom}
              y={PADDING_T}
              width={xTo - xFrom}
              height={PLOT_H}
              fill="rgba(251, 191, 36, 0.08)"
            />
          );
        })}

        {/* dBm gridlines + labels (left side). */}
        {[-30, -50, -70, -90].map((dbm) => (
          <g key={dbm}>
            <line
              x1={PADDING_L}
              x2={VB_W - PADDING_R}
              y1={yForRssi(dbm)}
              y2={yForRssi(dbm)}
              stroke="var(--color-cyber-border)"
              strokeOpacity={0.4}
              strokeDasharray="2 4"
            />
            <text
              x={PADDING_L - 6}
              y={yForRssi(dbm) + 3}
              fontSize={9}
              fill="var(--color-cyber-muted)"
              textAnchor="end"
              fontFamily="monospace"
            >
              {dbm}
            </text>
          </g>
        ))}

        {/* Channel number labels (bottom). Print every Nth so they don't crash. */}
        {layout.channels.map((ch, idx) => {
          const skip =
            band === "6"
              ? idx % 8 !== 0
              : band === "5"
                ? idx % 2 !== 0 && ch !== channelMax && ch !== channelMin
                : false;
          const isPsc = layout.pscChannels?.has(ch);
          if (skip && !isPsc) return null;
          return (
            <g key={ch}>
              <text
                x={xForChannel(ch)}
                y={VB_H - 12}
                fontSize={9}
                fill="var(--color-cyber-muted)"
                textAnchor="middle"
                fontFamily="monospace"
              >
                {ch}
              </text>
              {isPsc && (
                <text
                  x={xForChannel(ch)}
                  y={VB_H - 2}
                  fontSize={6}
                  fill="var(--color-cyber-accent)"
                  textAnchor="middle"
                  fontFamily="monospace"
                  opacity={0.7}
                >
                  PSC
                </text>
              )}
            </g>
          );
        })}

        {/* AP trapezoids — aggregated to (ap_root, channel). */}
        {sorted.map((n) => {
          const widthMhz = n.widthMhz;
          const halfWidthSteps = widthMhz / layout.mhzPerStep / 2;
          const xCenter = xForChannel(n.channel);
          const xLeft = xForChannel(n.channel - halfWidthSteps);
          const xRight = xForChannel(n.channel + halfWidthSteps);
          const yPeak = yForRssi(n.rssiDbm);
          const yFloor = VB_H - PADDING_B;
          const shoulderMhz = 2.5;
          const shoulderSteps = shoulderMhz / layout.mhzPerStep;
          const xLeftFloor = xForChannel(
            n.channel - halfWidthSteps - shoulderSteps,
          );
          const xRightFloor = xForChannel(
            n.channel + halfWidthSteps + shoulderSteps,
          );
          const color = colorFromKey(n.apRoot);
          const path = `M ${xLeftFloor} ${yFloor} L ${xLeft} ${yPeak} L ${xRight} ${yPeak} L ${xRightFloor} ${yFloor} Z`;
          const showLabel = xRight - xLeft > 50 && n.rssiDbm > -85;
          const isHovered = hoverKey === n.key;
          return (
            <g
              key={n.key}
              onMouseEnter={() => setHoverKey(n.key)}
              style={{ cursor: "pointer" }}
            >
              <path
                d={path}
                fill={color}
                fillOpacity={isHovered ? 0.42 : 0.22}
                stroke={color}
                strokeWidth={isHovered ? 2.2 : 1.3}
              />
              {showLabel && (
                <text
                  x={xCenter}
                  y={Math.max(PADDING_T + 10, yPeak - 4)}
                  fontSize={10}
                  fill={color}
                  textAnchor="middle"
                  fontFamily="monospace"
                  style={{ paintOrder: "stroke", pointerEvents: "none" }}
                  stroke="var(--color-cyber-bg-2)"
                  strokeWidth={3}
                >
                  {n.ssid}
                  {n.vapCount > 1 ? ` ×${n.vapCount}` : ""}
                </text>
              )}
            </g>
          );
        })}

        {/* Current + recommended channel markers — drawn last, on top. */}
        {currentChannel !== undefined && (
          <line
            x1={xForChannel(currentChannel)}
            x2={xForChannel(currentChannel)}
            y1={PADDING_T}
            y2={VB_H - PADDING_B}
            stroke="var(--color-cyber-accent)"
            strokeWidth={2}
            strokeOpacity={0.9}
          />
        )}
        {recommendedChannel !== undefined &&
          recommendedChannel !== currentChannel && (
            <line
              x1={xForChannel(recommendedChannel)}
              x2={xForChannel(recommendedChannel)}
              y1={PADDING_T}
              y2={VB_H - PADDING_B}
              stroke="#fbbf24"
              strokeWidth={2}
              strokeOpacity={0.9}
              strokeDasharray="4 3"
            />
          )}
      </svg>

      {/* Hover panel — sticks below the chart, doesn't overlap. */}
      {hovered && (
        <div className="mt-2 text-[10px] font-mono flex items-center gap-3 flex-wrap text-[color:var(--color-cyber-muted)]">
          <span
            className="inline-block w-2 h-2 rounded"
            style={{ background: colorFromKey(hovered.apRoot) }}
          />
          <span className="text-[color:var(--color-cyber-accent)]">
            {hovered.ssid}
          </span>
          {hovered.vapCount > 1 && (
            <span>
              {hovered.vapCount} VAPs sur ce canal
            </span>
          )}
          {hovered.vendor && <span>· {hovered.vendor}</span>}
          <span>· ch {hovered.channel}</span>
          <span>· {hovered.widthMhz} MHz</span>
          <span>· {hovered.rssiDbm} dBm</span>
          <span>· ap_root {hovered.apRoot}</span>
        </div>
      )}
    </div>
  );
}
