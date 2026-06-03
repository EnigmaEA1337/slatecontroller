/**
 * RSSI → distance estimation.
 *
 * Uses the Free Space Path Loss model :
 *
 *    d_m = 10 ^ ((TxPower_dBm − RSSI − 20·log10(freq_MHz) + 27.55) / 20)
 *
 * with TxPower assumed at +20 dBm (the typical "100 mW" cap for indoor
 * APs across regulatory domains). The formula returns line-of-sight
 * distance — real-world walls/floors/interference can multiply that by
 * 3-10x. We therefore expose the raw number AND a coarse bucket
 * (proche / moyen / loin / très loin) so the UI doesn't lie about
 * the precision.
 */

import type { WifiBand } from "@/types/wifi";

const ASSUMED_TX_POWER_DBM = 20; // 100 mW, indoor cap in most regdomains

// Per-band representative frequency (centre channel) — close enough for
// the FSPL exponent ; we don't try to be exact per-channel.
const BAND_FREQ_MHZ: Record<WifiBand, number> = {
  "2": 2442, // ch 7
  "5": 5500, // ch 100, middle of UNII
  "6": 6435, // mid 6 GHz UNII-5/6/7
};

export type DistanceBucket = "near" | "medium" | "far" | "very_far";

export interface DistanceEstimate {
  meters: number;          // FSPL prediction, raw — for reference only
  bucket: DistanceBucket;  // what to actually show to the operator
  label_fr: string;        // localised badge text
}

/** Bucket bounds are RSSI-based rather than meters-based : RSSI thresholds
 *  are much more predictable than the FSPL distance which over-estimates
 *  wildly indoors. */
export function bucketFromRssi(rssi_dbm: number): DistanceBucket {
  if (rssi_dbm >= -55) return "near";
  if (rssi_dbm >= -70) return "medium";
  if (rssi_dbm >= -85) return "far";
  return "very_far";
}

export function bucketLabel(bucket: DistanceBucket): string {
  return {
    near: "proche",
    medium: "moyen",
    far: "loin",
    very_far: "très loin",
  }[bucket];
}

export function bucketRangeM(bucket: DistanceBucket): string {
  return {
    near: "< 10 m",
    medium: "10-30 m",
    far: "30-80 m",
    very_far: "> 80 m",
  }[bucket];
}

export function bucketColor(bucket: DistanceBucket): string {
  return {
    near: "#5ae8a8",
    medium: "#ffb547",
    far: "#ff8c5a",
    very_far: "#ff3a52",
  }[bucket];
}

export function estimateDistance(
  band: WifiBand,
  rssi_dbm: number,
): DistanceEstimate {
  const freq = BAND_FREQ_MHZ[band] ?? BAND_FREQ_MHZ["5"];
  const exponent =
    (ASSUMED_TX_POWER_DBM - rssi_dbm - 20 * Math.log10(freq) + 27.55) / 20;
  const meters = Math.max(0.5, Math.pow(10, exponent));
  const bucket = bucketFromRssi(rssi_dbm);
  return {
    meters,
    bucket,
    label_fr: bucketLabel(bucket),
  };
}
