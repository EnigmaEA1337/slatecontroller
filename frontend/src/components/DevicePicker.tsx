import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, ChevronDown, Check } from "lucide-react";

import { listDevices } from "@/api/devices";
import {
  getActiveDevice,
  setActiveDevice,
  subscribeActiveDevice,
} from "@/lib/device-context";
import { useModalA11y } from "@/hooks/useModalA11y";

/**
 * Sidebar device picker. Shows the currently-selected device and lets
 * the user switch context — all subsequent backend requests will carry
 * `?device=<slug>` (wired in `api/client.ts`).
 *
 * "Auto" (null) = use the backend's default device. Hidden until the
 * user has registered more than one device, since the picker is mostly
 * noise in single-Slate setups.
 *
 * On device switch:
 *   - `setActiveDevice(slug)` writes localStorage + broadcasts a
 *     `slate:active-device-changed` event
 *   - We invalidate the entire react-query cache so every visible
 *     widget refetches against the new device. This avoids flashes of
 *     stale data (e.g. seeing the Slate's RAM gauge while we now
 *     pretend we're looking at a Mudi).
 */
export default function DevicePicker() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  // Mirror localStorage into React state so re-renders are correct.
  const [active, setActive] = useState<string | null>(getActiveDevice());

  useEffect(
    () =>
      subscribeActiveDevice((slug) => {
        setActive(slug);
      }),
    [],
  );

  const devicesQ = useQuery({
    queryKey: ["devices"],
    queryFn: listDevices,
    staleTime: 30_000,
  });

  // Hide the picker entirely for single-device users : the "Default"
  // shortcut already covers the only meaningful choice, and the chrome
  // takes sidebar real estate that's better used for nav.
  const devices = devicesQ.data ?? [];
  if (devices.length <= 1) return null;

  const defaultDevice = devices.find((d) => d.is_default) ?? devices[0];
  const currentSlug = active ?? defaultDevice?.slug ?? null;
  const currentLabel =
    devices.find((d) => d.slug === currentSlug)?.label ||
    currentSlug ||
    "—";

  function pick(slug: string | null) {
    setActiveDevice(slug);
    setOpen(false);
    // Hard invalidate every query — many of them are device-scoped
    // (slate status, AdGuard, hardening, profiles' active state…) and
    // we don't want a flash of stale content from the previous device.
    qc.invalidateQueries();
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex w-full items-center gap-2 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/60 px-3 py-2 text-left text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)] transition hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/8"
      >
        <Cpu className="h-3.5 w-3.5 shrink-0 text-[color:var(--color-cyber-accent)]" />
        <span className="cyber-label text-[9px] text-[color:var(--color-cyber-muted)]">
          device
        </span>
        <span className="ml-auto flex items-center gap-1 truncate text-[11px] font-bold text-[color:var(--color-cyber-fg)]">
          <span className="truncate">{currentLabel}</span>
          <ChevronDown
            className={`h-3 w-3 shrink-0 transition-transform ${
              open ? "rotate-180" : ""
            }`}
          />
        </span>
      </button>

      {open && (
        <DevicePickerDropdown
          onClose={() => setOpen(false)}
          devices={devices}
          currentSlug={currentSlug}
          defaultSlug={defaultDevice?.slug ?? null}
          onPick={pick}
        />
      )}
    </div>
  );
}

function DevicePickerDropdown({
  onClose,
  devices,
  currentSlug,
  defaultSlug,
  onPick,
}: {
  onClose: () => void;
  devices: Array<{ slug: string; label: string; is_default: boolean; status: string }>;
  currentSlug: string | null;
  defaultSlug: string | null;
  onPick: (slug: string | null) => void;
}) {
  const ref = useModalA11y<HTMLDivElement>(onClose);

  return (
    <div
      ref={ref}
      role="listbox"
      className="absolute bottom-full left-0 right-0 z-30 mb-1 max-h-80 overflow-y-auto border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-bg-2)] py-1 shadow-lg"
    >
      <button
        type="button"
        role="option"
        aria-selected={currentSlug === defaultSlug && currentSlug !== null}
        onClick={() => onPick(null)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] hover:bg-[color:var(--color-cyber-accent)]/8"
      >
        <Check
          className={`h-3 w-3 shrink-0 ${
            currentSlug === null
              ? "text-[color:var(--color-cyber-accent)]"
              : "opacity-0"
          }`}
        />
        <span className="cyber-label text-[10px] text-[color:var(--color-cyber-muted)]">
          auto
        </span>
        <span className="ml-1 text-[10px] text-[color:var(--color-cyber-dim)]">
          (default: {defaultSlug ?? "—"})
        </span>
      </button>
      <div className="cyber-hatch my-1 h-px w-full" />
      {devices.map((d) => (
        <button
          key={d.slug}
          type="button"
          role="option"
          aria-selected={currentSlug === d.slug}
          onClick={() => onPick(d.slug)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] hover:bg-[color:var(--color-cyber-accent)]/8"
        >
          <Check
            className={`h-3 w-3 shrink-0 ${
              currentSlug === d.slug
                ? "text-[color:var(--color-cyber-accent)]"
                : "opacity-0"
            }`}
          />
          <span className="truncate font-bold uppercase tracking-wider">
            {d.label || d.slug}
          </span>
          {d.is_default && (
            <span className="cyber-chip cyber-chip-ok ml-auto !text-[9px]">
              default
            </span>
          )}
          {d.status === "error" && (
            <span className="cyber-chip cyber-chip-on ml-auto !text-[9px]">
              err
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
