/**
 * ISO-3166-1 alpha-2 (lowercase) → approximate geographic centre
 * `[longitude, latitude]` for plotting Tor relays on a world map.
 *
 * Source : public-domain country centroids (rounded to 1 decimal).
 * The Tor relay network skews HEAVILY toward Europe + the US — every
 * country shown below has historically hosted at least one relay over
 * the past few years (data : metrics.torproject.org).
 *
 * Unknown / not-listed countries fall back to a "?" marker at (0, 0).
 */
export const COUNTRY_COORDS: Record<string, [number, number]> = {
  // Europe — by far the densest part of the Tor relay map.
  de: [10.5, 51.2], fr: [2.5, 46.6], nl: [5.3, 52.1], ch: [8.2, 46.8],
  gb: [-2.0, 54.5], se: [15.0, 62.0], fi: [26.0, 64.0], no: [10.0, 62.5],
  dk: [10.0, 56.0], at: [14.5, 47.5], be: [4.5, 50.5], lu: [6.1, 49.8],
  ie: [-8.0, 53.0], it: [12.5, 42.8], es: [-3.7, 40.0], pt: [-8.0, 39.5],
  pl: [19.5, 52.0], cz: [15.5, 49.8], sk: [19.5, 48.7], hu: [19.5, 47.2],
  ro: [25.0, 45.9], bg: [25.5, 42.7], gr: [22.0, 39.0], hr: [16.0, 45.2],
  si: [14.8, 46.1], rs: [21.0, 44.0], ba: [17.8, 44.0], mk: [21.7, 41.6],
  al: [20.0, 41.0], me: [19.3, 42.7], xk: [21.0, 42.6],
  ee: [25.0, 58.8], lv: [25.0, 56.9], lt: [23.9, 55.3],
  ua: [31.0, 49.0], by: [28.0, 53.7], md: [28.5, 47.0],
  ru: [105.0, 60.0], is: [-18.0, 65.0], mt: [14.5, 35.9], cy: [33.4, 35.1],
  // North America.
  us: [-99.0, 39.8], ca: [-106.0, 56.0], mx: [-102.0, 23.6],
  // Asia.
  jp: [138.0, 36.0], kr: [127.8, 36.5], cn: [104.0, 35.0], hk: [114.2, 22.4],
  tw: [121.0, 24.0], sg: [103.8, 1.4], my: [101.7, 4.2], th: [101.0, 13.7],
  vn: [108.0, 16.1], id: [113.9, -2.5], ph: [121.8, 12.9],
  in: [78.9, 22.0], pk: [69.3, 30.4], bd: [90.4, 23.7], il: [34.9, 31.0],
  tr: [35.2, 39.0], ae: [54.0, 24.0], sa: [45.0, 24.0], qa: [51.2, 25.3],
  kz: [66.9, 48.0], uz: [64.6, 41.4], am: [44.5, 40.1], ge: [43.4, 42.3],
  ir: [53.7, 32.4], iq: [43.7, 33.2],
  // Africa.
  za: [22.9, -30.6], eg: [30.8, 26.8], ng: [8.7, 9.1], ma: [-7.1, 31.8],
  ke: [37.9, -0.0], gh: [-1.0, 7.9], tn: [9.5, 33.9], dz: [1.7, 28.0],
  // Oceania.
  au: [134.5, -25.7], nz: [171.5, -41.0],
  // South + Central America.
  br: [-51.9, -14.2], ar: [-63.6, -34.6], cl: [-71.5, -35.7],
  co: [-74.3, 4.6], pe: [-75.0, -9.2], uy: [-55.8, -32.5], py: [-58.4, -23.4],
  bo: [-63.6, -16.3], ve: [-66.6, 7.4], cr: [-83.8, 9.7], pa: [-80.1, 8.5],
  do: [-70.5, 18.7], gt: [-90.3, 15.5], cu: [-77.8, 21.5], jm: [-77.3, 18.1],
};

export function coordsFor(cc: string | null | undefined): [number, number] | null {
  if (!cc) return null;
  const lower = cc.toLowerCase();
  return COUNTRY_COORDS[lower] ?? null;
}

/**
 * Tor exit-relay-friendly subset for the country picker on the Tor
 * settings card. Listed in the order of (roughly) "most exit
 * bandwidth → least" to make the dropdown's top choices the ones that
 * actually work without weird captchas / network drops.
 *
 * Avoids countries known for adversarial GeoIP / heavy surveillance
 * (skip cn, ir, kp, etc. — those rarely host exit relays anyway).
 */
export const EXIT_COUNTRY_PICKS: { code: string; label: string }[] = [
  { code: "de", label: "🇩🇪 Allemagne" },
  { code: "nl", label: "🇳🇱 Pays-Bas" },
  { code: "fr", label: "🇫🇷 France" },
  { code: "us", label: "🇺🇸 États-Unis" },
  { code: "ch", label: "🇨🇭 Suisse" },
  { code: "se", label: "🇸🇪 Suède" },
  { code: "ca", label: "🇨🇦 Canada" },
  { code: "gb", label: "🇬🇧 Royaume-Uni" },
  { code: "fi", label: "🇫🇮 Finlande" },
  { code: "at", label: "🇦🇹 Autriche" },
  { code: "ro", label: "🇷🇴 Roumanie" },
  { code: "lu", label: "🇱🇺 Luxembourg" },
  { code: "is", label: "🇮🇸 Islande" },
  { code: "no", label: "🇳🇴 Norvège" },
  { code: "dk", label: "🇩🇰 Danemark" },
  { code: "be", label: "🇧🇪 Belgique" },
  { code: "ie", label: "🇮🇪 Irlande" },
  { code: "es", label: "🇪🇸 Espagne" },
  { code: "it", label: "🇮🇹 Italie" },
  { code: "cz", label: "🇨🇿 République tchèque" },
  { code: "pl", label: "🇵🇱 Pologne" },
  { code: "jp", label: "🇯🇵 Japon" },
  { code: "au", label: "🇦🇺 Australie" },
  { code: "sg", label: "🇸🇬 Singapour" },
  { code: "hk", label: "🇭🇰 Hong Kong" },
];

export function flagFor(cc: string | null | undefined): string {
  if (!cc || cc.length !== 2) return "🌐";
  const code = cc.toLowerCase();
  // Convert "ch" -> 🇨🇭 using regional indicator symbols.
  const base = 0x1f1e6 - "a".charCodeAt(0);
  const a = code.charCodeAt(0) + base;
  const b = code.charCodeAt(1) + base;
  return String.fromCodePoint(a, b);
}
