import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Sparkles, X } from "lucide-react";
import { getSsidSuggestions } from "@/api/wifi";
import type { SsidSuggestionsLibrary } from "@/types/wifi-suggestions";
import { cn } from "@/lib/utils";

/** Inline picker shown inside the Wi-Fi form. Click a chip → ssid_name is filled. */
export default function SsidSuggestionsPicker({
  onPick,
  currentValue,
}: {
  onPick: (name: string) => void;
  currentValue?: string;
}) {
  const [open, setOpen] = useState(false);
  const [universeFilter, setUniverseFilter] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery<SsidSuggestionsLibrary>({
    queryKey: ["ssid-suggestions"],
    queryFn: getSsidSuggestions,
    enabled: open,
  });

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="cyber-button-ghost inline-flex items-center gap-2 px-3 py-1.5 text-[10px]"
      >
        <Sparkles className="h-3 w-3" />
        Suggestions cyberpunk
        {open ? (
          <ChevronUp className="h-3 w-3" />
        ) : (
          <ChevronDown className="h-3 w-3" />
        )}
      </button>

      {open && (
        <div className="mt-3 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)]/60 p-3">
          {isLoading && (
            <p className="cyber-label cyber-cursor text-[10px]">chargement library</p>
          )}
          {isError && (
            <p className="text-[11px] text-[color:var(--color-cyber-accent)]">
              library indisponible
            </p>
          )}
          {data && (
            <SuggestionsBody
              data={data}
              universeFilter={universeFilter}
              setUniverseFilter={setUniverseFilter}
              categoryFilter={categoryFilter}
              setCategoryFilter={setCategoryFilter}
              currentValue={currentValue}
              onPick={(name) => {
                onPick(name);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function SuggestionsBody({
  data,
  universeFilter,
  setUniverseFilter,
  categoryFilter,
  setCategoryFilter,
  currentValue,
  onPick,
}: {
  data: SsidSuggestionsLibrary;
  universeFilter: string | null;
  setUniverseFilter: (u: string | null) => void;
  categoryFilter: string | null;
  setCategoryFilter: (c: string | null) => void;
  currentValue?: string;
  onPick: (name: string) => void;
}) {
  const universes = Array.from(
    new Set(
      Object.values(data.categories).flatMap((c) =>
        c.options.map((o) => o.universe),
      ),
    ),
  ).sort();

  const categoryIds = Object.keys(data.categories);
  const visibleCategories = categoryFilter
    ? [categoryFilter].filter((c) => c in data.categories)
    : categoryIds;

  return (
    <div className="space-y-4">
      {/* Universe filter */}
      <div>
        <div className="cyber-label mb-2 text-[10px]">univers</div>
        <div className="flex flex-wrap gap-1.5">
          <FilterChip
            active={universeFilter === null}
            onClick={() => setUniverseFilter(null)}
          >
            tous
          </FilterChip>
          {universes.map((u) => (
            <FilterChip
              key={u}
              active={universeFilter === u}
              onClick={() =>
                setUniverseFilter(universeFilter === u ? null : u)
              }
            >
              {u.replace(/_/g, " ")}
            </FilterChip>
          ))}
        </div>
      </div>

      {/* Category filter */}
      <div>
        <div className="cyber-label mb-2 text-[10px]">catégorie d'usage</div>
        <div className="flex flex-wrap gap-1.5">
          <FilterChip
            active={categoryFilter === null}
            onClick={() => setCategoryFilter(null)}
          >
            toutes
          </FilterChip>
          {categoryIds.map((cid) => (
            <FilterChip
              key={cid}
              active={categoryFilter === cid}
              onClick={() =>
                setCategoryFilter(categoryFilter === cid ? null : cid)
              }
            >
              {data.categories[cid]?.label ?? cid}
            </FilterChip>
          ))}
        </div>
      </div>

      {/* Suggestions per category */}
      {visibleCategories.map((catId) => {
        const cat = data.categories[catId];
        if (!cat) return null;
        const filtered = cat.options.filter(
          (o) => !universeFilter || o.universe === universeFilter,
        );
        if (filtered.length === 0) return null;
        return (
          <div key={catId}>
            <div className="cyber-label mb-1.5 text-[10px]">
              {cat.label} · {filtered.length}
            </div>
            <p className="mb-2 text-[10px] italic text-[color:var(--color-cyber-dim)]">
              {cat.description}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {filtered.map((opt) => (
                <button
                  key={opt.name}
                  type="button"
                  onClick={() => onPick(opt.name)}
                  className={cn(
                    "cyber-chip cursor-pointer transition",
                    currentValue === opt.name
                      ? "cyber-chip-on"
                      : "hover:bg-[color:var(--color-cyber-accent)]/10 hover:text-[color:var(--color-cyber-accent)]",
                  )}
                  title={opt.universe.replace(/_/g, " ")}
                >
                  {opt.name}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "cyber-chip cursor-pointer transition",
        active && "cyber-chip-on",
      )}
    >
      {children}
    </button>
  );
}

/** Standalone panel for the Wi-Fi list page: show all universe combos as reference. */
export function UniverseCombosPanel() {
  const [openCombo, setOpenCombo] = useState<string | null>(null);
  const { data } = useQuery<SsidSuggestionsLibrary>({
    queryKey: ["ssid-suggestions"],
    queryFn: getSsidSuggestions,
  });

  if (!data || data.universe_combos.length === 0) return null;

  return (
    <section className="cyber-card mt-8 p-5">
      <h3 className="cyber-label mb-3 flex items-center gap-2">
        <Sparkles className="cyber-glow h-3 w-3" />
        combos cohérents par univers ({data.universe_combos.length})
      </h3>
      <p className="mb-4 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
        ▸ ensembles de 5 noms qui vont bien ensemble · cliquer pour voir le détail
      </p>
      <div className="flex flex-wrap gap-2">
        {data.universe_combos.map((combo) => (
          <button
            key={combo.id}
            type="button"
            onClick={() =>
              setOpenCombo(openCombo === combo.id ? null : combo.id)
            }
            className={cn(
              "cyber-chip cursor-pointer transition",
              openCombo === combo.id && "cyber-chip-on",
            )}
            title={combo.description}
          >
            {combo.label}
          </button>
        ))}
      </div>

      {openCombo &&
        data.universe_combos
          .filter((c) => c.id === openCombo)
          .map((combo) => (
            <div
              key={combo.id}
              className="mt-4 border border-[color:var(--color-cyber-border)] p-4"
            >
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <div className="cyber-glow text-sm font-bold uppercase tracking-[0.15em]">
                    {combo.label}
                  </div>
                  <p className="mt-1 text-[11px] italic text-[color:var(--color-cyber-muted)]">
                    {combo.description}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpenCombo(null)}
                  className="border border-transparent p-1 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              <dl className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-5">
                {Object.entries(combo.ssids).map(([cat, name]) => (
                  <div key={cat} className="flex flex-col">
                    <dt className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-dim)]">
                      {cat}
                    </dt>
                    <dd className="cyber-glow font-mono">{name}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
    </section>
  );
}
