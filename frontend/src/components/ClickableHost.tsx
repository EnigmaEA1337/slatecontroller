/**
 * Shared "clickable host" primitive.
 *
 * The controller's UI surfaces hostnames, IPv4/IPv6 literals and full
 * URLs in many places (cert SANs, device admin URLs, network gateways,
 * Tailscale peer IPs, etc.). Rendering them as plain text means the
 * operator has to copy-paste to test ; we wrap each one as a clickable
 * link that opens the implied https://<value>/ in a new tab.
 *
 * Behavior :
 *   - bare hostname or IPv4   → https://<value>/
 *   - bare IPv6 literal       → https://[<value>]/  (brackets required by the URL grammar)
 *   - explicit http(s)://...  → used as-is, no rewriting
 *   - empty / "—"             → rendered as muted text, not a link
 *
 * Styling : dotted underline that turns cyber-accent on hover, plus a
 * small external-link icon. Wrappers (`ClickableHostList`) handle the
 * comma separator between entries.
 */

import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

interface ClickableHostProps {
  value: string;
  /** Override the default `https://` scheme. Use `http` for plain-HTTP
   *  admin UIs (AdGuard on :3000, etc.). Ignored if `value` already
   *  carries an explicit scheme. */
  scheme?: "https" | "http";
  /** Optional path appended after the host (e.g. ":8080" or "/api/v2"). */
  pathSuffix?: string;
  /** Hide the external-link icon when set ; keeps tight tables clean. */
  hideIcon?: boolean;
  /** Extra Tailwind classes added to the anchor. */
  className?: string;
}

function buildHref(value: string, scheme: "https" | "http", pathSuffix: string): string {
  const trimmed = value.trim();
  if (/^https?:\/\//i.test(trimmed)) {
    // Already a full URL — append path suffix only if not already present.
    return pathSuffix && !trimmed.includes(pathSuffix)
      ? trimmed.replace(/\/$/, "") + pathSuffix
      : trimmed;
  }
  // IPv6 literal needs bracketing in the URL grammar (RFC 3986 §3.2.2).
  const isIPv6 = trimmed.includes(":") && !trimmed.startsWith("[");
  const hostPart = isIPv6 ? `[${trimmed}]` : trimmed;
  return `${scheme}://${hostPart}${pathSuffix || "/"}`;
}

export function ClickableHost({
  value,
  scheme = "https",
  pathSuffix = "",
  hideIcon = false,
  className,
}: ClickableHostProps) {
  const trimmed = value?.trim() ?? "";
  if (!trimmed || trimmed === "—") {
    return (
      <span className="text-[color:var(--color-cyber-muted)]">
        {trimmed || "—"}
      </span>
    );
  }
  const href = buildHref(trimmed, scheme, pathSuffix);
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-0.5 text-[color:var(--color-cyber-dim)] underline decoration-dotted underline-offset-2 hover:text-[color:var(--color-cyber-accent)]",
        className,
      )}
      title={`Ouvrir ${href} (nouvel onglet)`}
    >
      {trimmed}
      {!hideIcon && <ExternalLink className="h-2.5 w-2.5 opacity-60" />}
    </a>
  );
}

interface ClickableHostListProps {
  items: string[];
  scheme?: "https" | "http";
  pathSuffix?: string;
  separator?: string;
  hideIcon?: boolean;
  className?: string;
}

export function ClickableHostList({
  items,
  scheme = "https",
  pathSuffix = "",
  separator = ", ",
  hideIcon = false,
  className,
}: ClickableHostListProps) {
  if (!items || items.length === 0)
    return <span className="text-[color:var(--color-cyber-muted)]">—</span>;
  return (
    <span className={cn("font-mono", className)}>
      {items.map((s, i) => (
        <span key={`${s}-${i}`}>
          {i > 0 && (
            <span className="text-[color:var(--color-cyber-muted)]">
              {separator}
            </span>
          )}
          <ClickableHost
            value={s}
            scheme={scheme}
            pathSuffix={pathSuffix}
            hideIcon={hideIcon}
          />
        </span>
      ))}
    </span>
  );
}
