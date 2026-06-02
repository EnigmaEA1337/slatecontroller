import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play, RefreshCw, Terminal } from "lucide-react";

import { getTorLogs } from "@/api/tor";

/**
 * Cyberpunk-style log tail viewer for /var/log/tor/notices.log.
 *
 * Polls /api/tor/logs every 4s when not paused. Colorizes the standard
 * Tor severity tags so the operator can spot warnings + errors at a
 * glance. Auto-filters for "Bootstrapped" + circuit + warn/err lines so
 * the noisy ones don't bury the signal — toggle the "all" view to see
 * everything raw.
 */
export default function TorLogsViewer() {
  const [paused, setPaused] = useState(false);
  const [filterMode, setFilterMode] = useState<"signal" | "all">("signal");

  const logs = useQuery({
    queryKey: ["tor", "logs"],
    queryFn: () => getTorLogs(400),
    refetchInterval: paused ? false : 4_000,
  });

  const lines = useMemo(() => {
    const raw = logs.data?.lines ?? [];
    if (filterMode === "all") return raw;
    // "Signal" mode: keep Bootstrapped %, circuit messages, exit info,
    // warn/err lines, and any line mentioning bridges / GeoIP.
    return raw.filter((l) =>
      /Bootstrap|circuit|guard|\[warn\]|\[err\]|bridge|GeoIP|TransPort|DNSPort|ControlPort|exit/i.test(
        l,
      ),
    );
  }, [logs.data, filterMode]);

  const counts = useMemo(() => {
    const raw = logs.data?.lines ?? [];
    return {
      warns: raw.filter((l) => /\[warn\]/i.test(l)).length,
      errs: raw.filter((l) => /\[err\]/i.test(l)).length,
      total: raw.length,
    };
  }, [logs.data]);

  return (
    <section className="mt-6 cyber-panel rounded border border-zinc-700 bg-black/60">
      <header className="flex items-center justify-between gap-2 border-b border-zinc-800 px-3 py-2">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-purple-300" />
          <h2 className="cyber-heading text-sm text-purple-200">
            tor.log — notices.log
          </h2>
          <span className="cyber-chip cyber-chip-ghost px-1.5 py-0.5 text-[10px]">
            {counts.total} lignes
          </span>
          {counts.warns > 0 && (
            <span className="cyber-chip-ghost px-1.5 py-0.5 text-[10px] text-yellow-300">
              {counts.warns} warn
            </span>
          )}
          {counts.errs > 0 && (
            <span className="cyber-chip-ghost px-1.5 py-0.5 text-[10px] text-red-300">
              {counts.errs} err
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setFilterMode((m) => (m === "signal" ? "all" : "signal"))}
            className="cyber-chip-ghost px-1.5 py-0.5 text-[10px]"
            title="Toggle: signal vs raw"
          >
            {filterMode === "signal" ? "signal" : "tout"}
          </button>
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            className="cyber-chip-ghost p-1"
            title={paused ? "Reprendre" : "Pause"}
          >
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={() => logs.refetch()}
            className="cyber-chip-ghost p-1"
            title="Rafraîchir"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      <div className="max-h-[420px] overflow-auto font-mono text-[11px] leading-relaxed">
        {logs.isLoading && (
          <div className="px-3 py-6 text-center text-zinc-500">chargement…</div>
        )}
        {!logs.isLoading && lines.length === 0 && (
          <div className="px-3 py-6 text-center text-zinc-500">
            {logs.data ? (
              <>
                Aucune ligne (
                {filterMode === "signal"
                  ? "filtré — clique « tout » pour voir le brut"
                  : "le daemon n'a pas encore écrit dans notices.log"}
                )
              </>
            ) : (
              "log indisponible"
            )}
          </div>
        )}
        <ul>
          {lines.map((line, i) => (
            <LogLine key={i} text={line} />
          ))}
        </ul>
      </div>
    </section>
  );
}

function LogLine({ text }: { text: string }) {
  let cls = "text-zinc-400";
  if (/\[err\]/i.test(text)) cls = "text-red-300";
  else if (/\[warn\]/i.test(text)) cls = "text-yellow-300";
  else if (/Bootstrapped 100%/.test(text)) cls = "text-emerald-300";
  else if (/Bootstrapped/.test(text)) cls = "text-cyan-300";
  else if (/circuit/i.test(text)) cls = "text-purple-200";
  else if (/\[notice\]/i.test(text)) cls = "text-zinc-300";
  return (
    <li className={`whitespace-pre-wrap px-3 py-0.5 ${cls} hover:bg-zinc-900/60`}>
      {text}
    </li>
  );
}
