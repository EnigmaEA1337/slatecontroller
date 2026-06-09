// Surveillance sessions list page (Q2-C).
// Active sessions on top with live counter ; completed/cancelled below.
// Click a session → navigate to /networks/surveillance/{id} for the
// timeline.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Plus,
  RadioTower,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  type SessionStatus,
  type SurveillanceSession,
  SURVEILLANCE_PRESETS,
  cancelSurveillanceSession,
  createSurveillanceSession,
  deleteSurveillanceSession,
  listSurveillanceSessions,
} from "@/api/surveillance";
import NewSessionModal from "@/components/NewSessionModal";
import { usePinConfirm } from "@/hooks/usePinConfirm";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export default function SurveillancePage() {
  const t = useT();
  const qc = useQueryClient();
  const sessions = useQuery({
    queryKey: ["wifi", "surveillance", "list"],
    queryFn: () => listSurveillanceSessions(),
    refetchInterval: 10_000,
  });

  const [createOpen, setCreateOpen] = useState(false);

  const createMut = useMutation({
    mutationFn: (body: Parameters<typeof createSurveillanceSession>[0]) =>
      createSurveillanceSession(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wifi", "surveillance"] });
      setCreateOpen(false);
    },
  });
  const cancelMut = useMutation({
    mutationFn: (id: number) => cancelSurveillanceSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wifi", "surveillance"] }),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteSurveillanceSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wifi", "surveillance"] }),
  });

  // PIN gate on session deletion — destructive action (cascades scans).
  // Operator types touchscreen PIN to confirm. Lockout/auto-erase kick
  // in if they fail too many times in autonomous mode.
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const pinGate = usePinConfirm({
    title: "Supprimer la session",
    description:
      pendingDeleteId !== null
        ? `Supprimer la session #${pendingDeleteId} et toutes ses passes ? Action irréversible.`
        : undefined,
    onConfirmed: () => {
      if (pendingDeleteId !== null) {
        delMut.mutate(pendingDeleteId);
        setPendingDeleteId(null);
      }
    },
  });
  const requestDelete = (id: number) => {
    setPendingDeleteId(id);
    pinGate.request();
  };

  const active = useMemo(
    () => sessions.data?.filter((s) => s.status === "active") ?? [],
    [sessions.data],
  );
  const past = useMemo(
    () => sessions.data?.filter((s) => s.status !== "active") ?? [],
    [sessions.data],
  );

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div className="cyber-label flex items-center gap-2">
          <RadioTower className="h-3 w-3" /> {t("net_surveillance.title")}
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="cyber-button px-3 py-1.5 text-xs"
        >
          <Plus className="h-3 w-3 inline mr-1" /> {t("net_surveillance.new_session")}
        </button>
      </header>

      <p className="text-xs text-[color:var(--color-cyber-muted)] max-w-3xl">
        {t("net_surveillance.subtitle")} La chronologie classe chaque BSSID
        en <span className="text-emerald-300">stable</span> ·{" "}
        <span className="text-amber-300">en limite</span> ·{" "}
        <span className="text-cyan-300">dérivant</span> ·{" "}
        <span className="text-slate-400">transitoire</span> selon sa présence
        et la variation de RSSI.
      </p>

      {active.length > 0 && (
        <section className="cyber-card cyber-card-accent p-3">
          <header className="cyber-label text-[10px] mb-2">
            actives ({active.length})
          </header>
          <div className="space-y-2">
            {active.map((s) => (
              <SessionRow
                key={s.id}
                s={s}
                onCancel={() => cancelMut.mutate(s.id)}
                onDelete={() => requestDelete(s.id)}
                cancelling={cancelMut.isPending}
                deleting={delMut.isPending}
              />
            ))}
          </div>
        </section>
      )}

      <section className="cyber-card p-3">
        <header className="cyber-label text-[10px] mb-2">
          archive ({past.length})
        </header>
        {past.length === 0 ? (
          <p className="text-xs text-[color:var(--color-cyber-muted)]">
            Aucune session arch&eacute;e — lancez votre premi&egrave;re surveillance.
          </p>
        ) : (
          <div className="space-y-2">
            {past.map((s) => (
              <SessionRow
                key={s.id}
                s={s}
                onCancel={() => {}}
                onDelete={() => requestDelete(s.id)}
                cancelling={false}
                deleting={delMut.isPending}
              />
            ))}
          </div>
        )}
      </section>

      <NewSessionModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={(body) => createMut.mutate(body)}
        presets={SURVEILLANCE_PRESETS}
        submitting={createMut.isPending}
      />
      {pinGate.modal}
    </div>
  );
}

function SessionRow({
  s,
  onCancel,
  onDelete,
  cancelling,
  deleting,
}: {
  s: SurveillanceSession;
  onCancel: () => void;
  onDelete: () => void;
  cancelling: boolean;
  deleting: boolean;
}) {
  const startedAt = new Date(s.started_at);
  const endedAt = s.ended_at ? new Date(s.ended_at) : null;
  const elapsedS =
    (endedAt ? endedAt.getTime() : Date.now()) - startedAt.getTime();
  const elapsedMin = Math.floor(elapsedS / 60_000);
  const targetMin = Math.floor(s.target_duration_s / 60);
  const progress = Math.min(
    100,
    Math.round((elapsedS / 1000 / s.target_duration_s) * 100),
  );
  return (
    <div className="flex items-center gap-3 p-2 border border-[color:var(--color-cyber-border)]/40 rounded-sm hover:border-[color:var(--color-cyber-border-strong)] transition-colors">
      <StatusIcon status={s.status} />
      <Link
        to={`/networks/surveillance/${s.id}`}
        className="flex-1 min-w-0 block"
      >
        <div className="text-sm font-mono truncate">
          #{s.id} · {s.name}
        </div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)] flex items-center gap-2 flex-wrap mt-0.5">
          <Clock className="h-3 w-3 shrink-0" />
          <span className="font-mono">
            {startedAt.toLocaleString("fr-FR")}
          </span>
          <span>·</span>
          <span>
            {elapsedMin}/{targetMin} min ({progress}%)
          </span>
          <span>·</span>
          <span>bandes&nbsp;{s.bands.replaceAll(",", " · ")} GHz</span>
          <span>·</span>
          <span>
            {s.total_passes} passes · {s.unique_bssids} BSSIDs
          </span>
          {s.location_label && (
            <>
              <span>·</span>
              <span className="text-cyan-300">📍 {s.location_label}</span>
            </>
          )}
        </div>
        {s.status === "active" && (
          <div className="mt-1 h-1 w-full bg-[color:var(--color-cyber-bg-2)] rounded-sm overflow-hidden">
            <div
              className="h-full bg-[color:var(--color-cyber-accent)]"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </Link>
      {s.status === "active" && (
        <button
          onClick={onCancel}
          disabled={cancelling}
          className="cyber-button-ghost px-2 py-1 text-[10px] shrink-0"
          title="Arrêter la session"
        >
          stop
        </button>
      )}
      <button
        onClick={onDelete}
        disabled={deleting}
        className="cyber-button-ghost p-1 shrink-0 text-[color:var(--color-cyber-muted)] hover:text-amber-300"
        title="Supprimer cette session + ses scans"
      >
        <Trash2 className="h-3 w-3" />
      </button>
      <Link
        to={`/networks/surveillance/${s.id}`}
        className="shrink-0 text-[color:var(--color-cyber-muted)]"
        title="Voir la timeline"
      >
        <ChevronRight className="h-4 w-4" />
      </Link>
    </div>
  );
}

function StatusIcon({ status }: { status: SessionStatus }) {
  if (status === "active") {
    return (
      <span
        className="h-2 w-2 rounded-full shrink-0"
        style={{
          background: "var(--color-cyber-accent)",
          boxShadow: "0 0 6px var(--color-cyber-accent)",
        }}
        title="En cours"
      />
    );
  }
  if (status === "completed") {
    return (
      <CheckCircle2
        className={cn("h-3 w-3 shrink-0 text-emerald-300")}
      />
    );
  }
  if (status === "cancelled") {
    return <XCircle className="h-3 w-3 shrink-0 text-slate-400" />;
  }
  return <AlertCircle className="h-3 w-3 shrink-0 text-amber-300" />;
}
