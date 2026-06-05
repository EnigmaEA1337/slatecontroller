// Vendor logo chip (OSINT Phase D).
//
// We could embed real SVG marks per brand, but actual logos vary too
// much (Apple silhouette, Intel "i", Google G…) and reproducing them
// pixel-perfect risks trademark issues. Instead we use a curated map
// of *brand colour* + *monogram* — instantly recognisable on a 16-20px
// chip, scales to any size, theme-agnostic, no asset bundling.
//
// Slugs come from backend/app/wifi/oui.py `_VENDOR_SLUGS`. Add new
// entries here when the backend gets new slug mappings.

import { cn } from "@/lib/utils";

interface BrandStyle {
  color: string;   // background colour
  label: string;   // 1-3 char monogram
  fg?: string;     // text colour (default white)
}

const STYLES: Record<string, BrandStyle> = {
  apple:        { color: "#1d1d1f", label: "" },
  ubiquiti:     { color: "#0090d0", label: "UI" },
  cisco:        { color: "#1ba0d7", label: "Cs" },
  aruba:        { color: "#ff8300", label: "Ar" },
  hp:           { color: "#0096d6", label: "HP" },
  intel:        { color: "#0071c5", label: "in" },
  samsung:      { color: "#1428a0", label: "SS" },
  huawei:       { color: "#cf0a2c", label: "Hw" },
  xiaomi:       { color: "#ff6900", label: "Mi" },
  oneplus:      { color: "#eb0029", label: "1+" },
  oppo:         { color: "#00a050", label: "Op" },
  google:       { color: "#4285f4", label: "G" },
  microsoft:    { color: "#00a4ef", label: "Ms" },
  mediatek:     { color: "#003f7f", label: "MT" },
  qualcomm:     { color: "#3253dc", label: "Qc" },
  broadcom:     { color: "#cc092f", label: "Bc" },
  realtek:      { color: "#2e8b57", label: "Rt" },
  netgear:      { color: "#b58e1a", label: "Ng" },
  tplink:       { color: "#4acbd6", label: "TP", fg: "#0d1b2a" },
  asus:         { color: "#2e3192", label: "As" },
  dlink:        { color: "#00aeef", label: "D" },
  mikrotik:     { color: "#293f54", label: "µT" },
  ruckus:       { color: "#cb0000", label: "Rk" },
  zte:          { color: "#017dc2", label: "ZT" },
  nokia:        { color: "#124191", label: "N" },
  amazon:       { color: "#ff9900", label: "A", fg: "#0d1b2a" },
  raspberrypi:  { color: "#c51a4a", label: "π" },
  sonos:        { color: "#0a0a0a", label: "So" },
  nintendo:     { color: "#e60012", label: "Nt" },
  sony:         { color: "#0a0a0a", label: "Sn" },
  lg:           { color: "#a50034", label: "LG" },
  philips:      { color: "#0066b2", label: "Ph" },
  freebox:      { color: "#c8232c", label: "Fb" },
  bouygues:     { color: "#f7a600", label: "Bg", fg: "#0d1b2a" },
  orange:       { color: "#ff7900", label: "O", fg: "#0d1b2a" },
  sfr:          { color: "#c4002a", label: "SFR" },
  glinet:       { color: "#d62b06", label: "GL" },
  espressif:    { color: "#e7352c", label: "Es" },
};

const FALLBACK: BrandStyle = { color: "#475569", label: "?" };

const SIZES = {
  sm: { dim: 14, font: 8 },
  md: { dim: 18, font: 9 },
  lg: { dim: 22, font: 11 },
};

export default function VendorLogo({
  slug,
  vendor,
  size = "md",
  withLabel = false,
  isRandomized = false,
  className,
}: {
  slug: string | undefined | null;
  vendor?: string;
  size?: keyof typeof SIZES;
  withLabel?: boolean;
  isRandomized?: boolean;
  className?: string;
}) {
  if (isRandomized) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-[10px] text-amber-300",
          className,
        )}
        title="MAC randomisée — vendor non identifiable"
      >
        🎭 {withLabel && "random"}
      </span>
    );
  }

  const style = (slug && STYLES[slug]) || FALLBACK;
  const dim = SIZES[size];
  const isFallback = !slug || !STYLES[slug];

  const chip = (
    <span
      className="inline-flex items-center justify-center shrink-0 font-bold leading-none"
      style={{
        width: dim.dim,
        height: dim.dim,
        background: style.color,
        color: style.fg ?? "#ffffff",
        fontSize: dim.font,
        borderRadius: 3,
        letterSpacing: "-0.02em",
      }}
      title={vendor || slug || "vendor inconnu"}
    >
      {isFallback && vendor
        ? vendor.slice(0, 2).toUpperCase()
        : style.label}
    </span>
  );

  if (!withLabel) {
    return <span className={className}>{chip}</span>;
  }

  const labelText = vendor || slug || "inconnu";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-[10px]",
        className,
      )}
    >
      {chip}
      <span
        className={cn(
          isFallback
            ? "text-[color:var(--color-cyber-muted)]"
            : "text-[color:var(--color-cyber-fg)]",
        )}
      >
        {labelText.length > 22 ? labelText.slice(0, 20) + "…" : labelText}
      </span>
    </span>
  );
}
