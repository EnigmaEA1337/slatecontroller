// Spectrum chart — band-by-band visualisation à la WiFi Explorer Pro.
//
// Each detected AP is rendered as a coloured trapezoid : center on its
// channel, half-width = bandwidth/2 (in channel-numbers for 5/6 GHz, in
// frequency-MHz for 2.4 GHz), height proportional to RSSI. Stronger APs
// fill more vertically + sit in front, weaker ones recede into the
// background.
//
// Colour is hashed from ``ap_root`` so VAPs of the same physical AP
// share their trapezoid colour with the tree-view group badge.
//
// Input shape : the ``NeighborAPView`` list straight from the scanner.
// We extract the bandwidth from ``ht_mode`` ("HT20"/"VHT80"/"HE160"/
// "EHT320"/…) — when it's unparseable we default to 20 MHz so the AP
// still shows up as a thin trapezoid rather than disappearing.

import { useMemo } from "react";

import type { NeighborAPView } from "@/types/wifi-radio";
import type { WifiBand } from "@/types/wifi";

interface Props {
  neighbors: NeighborAPView[];
  band: WifiBand;
  /** Optional height override — chart scales horizontally to its container. */
  height?: number;
}

// ---------- band geometry ----------

interface BandLayout {
  /** Channel numbers we lay out on the X axis (left-to-right). */
  channels: number[];
  /** How many MHz one channel-number step represents on this band's grid. */
  mhzPerStep: number;
  /** Pretty label printed below the axis. */
  label: string;
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
  },
  // 6 GHz : channels 1, 5, 9, …, 233 — every 20 MHz. We sample a
  // representative subset (channels with broad client compatibility
  // including PSC). Wi-Fi 7 320 MHz channels span 16 numbered-channel
  // steps so they cover almost the whole UNII-5/6/7/8 here.
  "6": {
    channels: Array.from({ length: 59 }, (_, i) => 1 + i * 4),
    mhzPerStep: 20,
    label: "6 GHz · 5945 → 7115 MHz",
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

// ---------- component ----------

export default function SpectrumChart({
  neighbors,
  band,
  height = 220,
}: Props) {
  const layout = BAND_LAYOUT[band];

  // Filter to the band first ; ignore the rest (the tree handles cross-band).
  const items = useMemo(
    () => neighbors.filter((n) => n.band === band && n.channel > 0),
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
    () => [...items].sort((a, b) => a.rssi_dbm - b.rssi_dbm),
    [items],
  );

  if (sorted.length === 0) {
    return (
      <div className="cyber-card p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Aucun AP {band} GHz à afficher dans le spectre.
      </div>
    );
  }

  return (
    <div className="cyber-card p-3">
      <header className="cyber-label text-[10px] mb-2 flex items-center justify-between">
        <span>spectre · {layout.label}</span>
        <span className="text-[color:var(--color-cyber-muted)]">
          {sorted.length} APs
        </span>
      </header>

      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        className="w-full block"
        style={{ background: "var(--color-cyber-bg-2)" }}
        preserveAspectRatio="none"
      >
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
          if (skip) return null;
          return (
            <text
              key={ch}
              x={xForChannel(ch)}
              y={VB_H - 10}
              fontSize={9}
              fill="var(--color-cyber-muted)"
              textAnchor="middle"
              fontFamily="monospace"
            >
              {ch}
            </text>
          );
        })}

        {/* AP trapezoids. */}
        {sorted.map((n) => {
          const widthMhz = widthFromHtMode(n.ht_mode);
          // Convert bandwidth to "channel-number steps" half-width.
          const halfWidthSteps = widthMhz / layout.mhzPerStep / 2;
          const xCenter = xForChannel(n.channel);
          const xLeft = xForChannel(n.channel - halfWidthSteps);
          const xRight = xForChannel(n.channel + halfWidthSteps);
          const yPeak = yForRssi(n.rssi_dbm);
          const yFloor = VB_H - PADDING_B;
          // A trapezoid : flat top across the channel width, sloping
          // shoulders down to floor over a ~3 MHz roll-off.
          const shoulderMhz = 2.5;
          const shoulderSteps = shoulderMhz / layout.mhzPerStep;
          const xLeftFloor = xForChannel(
            n.channel - halfWidthSteps - shoulderSteps,
          );
          const xRightFloor = xForChannel(
            n.channel + halfWidthSteps + shoulderSteps,
          );
          const color = colorFromKey(n.ap_root || n.bssid);
          const path = `M ${xLeftFloor} ${yFloor} L ${xLeft} ${yPeak} L ${xRight} ${yPeak} L ${xRightFloor} ${yFloor} Z`;
          const showLabel = xRight - xLeft > 50 && n.rssi_dbm > -85;
          return (
            <g key={n.bssid}>
              <path
                d={path}
                fill={color}
                fillOpacity={0.22}
                stroke={color}
                strokeWidth={1.3}
              />
              {showLabel && (
                <text
                  x={xCenter}
                  y={Math.max(PADDING_T + 10, yPeak - 4)}
                  fontSize={10}
                  fill={color}
                  textAnchor="middle"
                  fontFamily="monospace"
                  style={{ paintOrder: "stroke" }}
                  stroke="var(--color-cyber-bg-2)"
                  strokeWidth={3}
                >
                  {n.ssid || "<hidden>"}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
