/**
 * Setup Status — aggregated readiness check for the controller.
 *
 * Single page that tells the operator at a glance whether every piece
 * of infrastructure is wired up. Each check is independent and
 * cached separately so a flaky probe doesn't break the rest of the
 * view. Status taxonomy :
 *
 *   - ok        ✓ everything in place
 *   - warn      ⚠ partial / suboptimal but functional
 *   - missing   ✗ not configured
 *   - error     ✗ probe failed (network / auth)
 *
 * The list is read-only ; each row links to the relevant settings
 * page where the operator actually fixes things.
 */

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  Loader2,
  XCircle,
} from "lucide-react";
import { Link } from "react-router-dom";
import { getInternalCAStatus } from "@/api/internalCa";
import { getControllerHttpsState } from "@/api/controllerHttps";
import { listDevices } from "@/api/devices";
import {
  getSshKeypairStatus,
  getTailnetAdminIps,
} from "@/api/settings";
import { cn } from "@/lib/utils";

type CheckStatus = "ok" | "warn" | "missing" | "error" | "loading";

interface CheckRow {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
  link: string;
  linkLabel: string;
}

export default function SetupStatus() {
  const sshQ = useQuery({
    queryKey: ["setup", "ssh"],
    queryFn: getSshKeypairStatus,
    refetchOnWindowFocus: false,
  });
  const devicesQ = useQuery({
    queryKey: ["setup", "devices"],
    queryFn: listDevices,
    refetchOnWindowFocus: false,
  });
  const httpsQ = useQuery({
    queryKey: ["setup", "controller-https"],
    queryFn: getControllerHttpsState,
    refetchOnWindowFocus: false,
  });
  const caQ = useQuery({
    queryKey: ["setup", "internal-ca"],
    queryFn: getInternalCAStatus,
    refetchOnWindowFocus: false,
  });
  // The controller-callback URLs check is removed : no consumer reads
  // these URLs today (webhooks not wired up). Keeping them in the Setup
  // Status would flag a false "warn" forever. Restore once anti-theft
  // webhooks are implemented (cf. Phase 1 anti-forensics in the todo).
  const tailnetAdminQ = useQuery({
    queryKey: ["setup", "tailnet-admin"],
    queryFn: getTailnetAdminIps,
    refetchOnWindowFocus: false,
  });

  // Two ordered groups : controller prerequisites first (what needs to be
  // in place BEFORE the operator can sensibly onboard a device), then
  // the device-onboarding side. Within each group, items follow the
  // natural dependency order.
  const foundationChecks: CheckRow[] = [
    /* ---- Controller HTTPS (Tailscale Serve) ---- */
    (() => {
      const link = "/settings/controller-https";
      const linkLabel = "HTTPS Controller";
      if (httpsQ.isLoading)
        return loading("https", "HTTPS Controller (Tailscale)", link, linkLabel);
      if (httpsQ.isError)
        return errored("https", "HTTPS Controller (Tailscale)", link, linkLabel, httpsQ.error);
      const s = httpsQ.data!;
      if (!s.cli_available)
        return {
          id: "https",
          label: "HTTPS Controller (Tailscale)",
          status: "missing",
          detail: "CLI tailscale absent du backend container (rebuild requis).",
          link,
          linkLabel,
        };
      if (!s.daemon_reachable)
        return {
          id: "https",
          label: "HTTPS Controller (Tailscale)",
          status: "error",
          detail: "tailscaled injoignable — sidecar pas démarré ou non auth.",
          link,
          linkLabel,
        };
      if (!s.https_enabled)
        return {
          id: "https",
          label: "HTTPS Controller (Tailscale)",
          status: "warn",
          detail: `Sidecar UP (${s.tailnet_hostname || "?"}) mais Serve non configuré.`,
          link,
          linkLabel,
        };
      const days = s.cert?.days_remaining;
      return {
        id: "https",
        label: "HTTPS Controller (Tailscale)",
        status: days !== null && days !== undefined && days < 30 ? "warn" : "ok",
        detail: `${s.public_url ?? "—"}${days !== undefined && days !== null ? ` · cert J-${days}` : ""}`,
        link,
        linkLabel,
      };
    })(),

    /* ---- Internal CA ---- */
    (() => {
      const link = "/settings/internal-ca";
      const linkLabel = "CA interne";
      if (caQ.isLoading) return loading("ca", "CA interne + cert Slate", link, linkLabel);
      if (caQ.isError)
        return errored("ca", "CA interne + cert Slate", link, linkLabel, caQ.error);
      const c = caQ.data!;
      if (!c.initialized)
        return {
          id: "ca",
          label: "CA interne + cert Slate",
          status: "missing",
          detail: "Root CA pas initialisé — pas de cert signé pour le Slate.",
          link,
          linkLabel,
        };
      if (!c.slate_cert_serial_hex)
        return {
          id: "ca",
          label: "CA interne + cert Slate",
          status: "warn",
          detail: `Root CA OK (${c.issued_count} cert(s) émis) mais aucun poussé sur le Slate.`,
          link,
          linkLabel,
        };
      return {
        id: "ca",
        label: "CA interne + cert Slate",
        status: "ok",
        detail: `${c.config.subject.common_name} · cert serial ${c.slate_cert_serial_hex.slice(0, 16)}…`,
        link,
        linkLabel,
      };
    })(),

  ];

  // Device-onboarding side : these only make sense once the foundations
  // above are in place. The operator goes through them AFTER the
  // controller is reachable + trusted.
  const slateChecks: CheckRow[] = [
    /* ---- Slate adoption ---- */
    (() => {
      const link = "/devices";
      const linkLabel = "Devices";
      if (devicesQ.isLoading)
        return loading("device", "Slate adopté", link, linkLabel);
      if (devicesQ.isError)
        return errored("device", "Slate adopté", link, linkLabel, devicesQ.error);
      const devs = devicesQ.data ?? [];
      const adopted = devs.filter((d) => d.status === "adopted");
      if (devs.length === 0)
        return {
          id: "device",
          label: "Slate adopté",
          status: "missing",
          detail:
            "Aucun équipement enregistré. Une fois les fondations vertes, adopte ton Slate ici.",
          link,
          linkLabel,
        };
      if (adopted.length === 0)
        return {
          id: "device",
          label: "Slate adopté",
          status: "warn",
          detail: `${devs.length} équipement(s) enregistré(s) mais aucun adopté (hardening tasks pas lancées).`,
          link,
          linkLabel,
        };
      return {
        id: "device",
        label: "Slate adopté",
        status: "ok",
        detail: `${adopted.length} équipement(s) adopté(s).`,
        link,
        linkLabel,
      };
    })(),

    /* ---- SSH keypair ---- */
    (() => {
      const link = "/settings/ssh-key";
      const linkLabel = "SSH Keypair";
      if (sshQ.isLoading) return loading("ssh", "Clé SSH déployée", link, linkLabel);
      if (sshQ.isError)
        return errored("ssh", "Clé SSH déployée", link, linkLabel, sshQ.error);
      const s = sshQ.data!;
      if (!s.generated)
        return {
          id: "ssh",
          label: "Clé SSH déployée",
          status: "missing",
          detail: "Aucune paire de clés générée — auth Slate reste en password.",
          link,
          linkLabel,
        };
      if (!s.deployed_to_slate)
        return {
          id: "ssh",
          label: "Clé SSH déployée",
          status: "warn",
          detail: "Clé générée mais pas déployée sur le Slate (`authorized_keys` absent).",
          link,
          linkLabel,
        };
      return {
        id: "ssh",
        label: "Clé SSH déployée",
        status: "ok",
        detail: `Fingerprint ${s.fingerprint_sha256?.slice(0, 32) ?? "—"}…`,
        link,
        linkLabel,
      };
    })(),

    /* ---- Tailnet admin whitelist ---- */
    (() => {
      const link = "/settings/tailnet-admin";
      const linkLabel = "Tailnet admin";
      if (tailnetAdminQ.isLoading)
        return loading("tailadmin", "Filtrage admin tailnet", link, linkLabel);
      if (tailnetAdminQ.isError)
        return errored(
          "tailadmin",
          "Filtrage admin tailnet",
          link,
          linkLabel,
          tailnetAdminQ.error,
        );
      const t = tailnetAdminQ.data!;
      const count = (t.admin_ips ?? []).length;
      if (count === 0)
        return {
          id: "tailadmin",
          label: "Filtrage admin tailnet",
          status: "warn",
          detail:
            "Désactivé (whitelist vide). N'importe quel peer tailnet peut atteindre l'admin du Slate.",
          link,
          linkLabel,
        };
      return {
        id: "tailadmin",
        label: "Filtrage admin tailnet",
        status: "ok",
        detail: `Actif dans tous les profils — ${count} peer(s) autorisé(s) à atteindre l'admin.`,
        link,
        linkLabel,
      };
    })(),
  ];

  const allChecks = [...foundationChecks, ...slateChecks];
  const okCount = allChecks.filter((c) => c.status === "ok").length;
  const warnCount = allChecks.filter((c) => c.status === "warn").length;
  const missingCount = allChecks.filter(
    (c) => c.status === "missing" || c.status === "error",
  ).length;

  const foundationsReady = foundationChecks.every((c) => c.status === "ok");

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <CheckCircle2 className="cyber-glow h-3 w-3" />
          controller settings · état de configuration
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text="SETUP STATUS"
        >
          SETUP STATUS
        </h1>
        <p className="mt-2 max-w-2xl text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          Vue agrégée de la configuration du controller. Permet de vérifier
          d'un coup d'œil que toutes les briques (Tailscale, CA, SSH, Slate,
          callbacks) sont opérationnelles avant un déploiement ou après une
          mise à jour.
        </p>
      </header>

      {/* Summary banner */}
      <section className="cyber-panel mb-6 grid grid-cols-3 gap-4 p-4">
        <SummaryBox label="OK" value={okCount} kind="ok" />
        <SummaryBox label="Avertissements" value={warnCount} kind="warn" />
        <SummaryBox label="Manquants" value={missingCount} kind="missing" />
      </section>

      {/* Group 1 — Controller foundations */}
      <CheckSection
        title="1 · Fondations du controller"
        subtitle="À mettre en place AVANT d'intégrer un Slate. Toute la suite en dépend."
        checks={foundationChecks}
        ready={foundationsReady}
      />

      {/* Group 2 — Slate onboarding */}
      <CheckSection
        title="2 · Intégration du Slate"
        subtitle={
          foundationsReady
            ? "Fondations OK — tu peux dérouler l'intégration de l'équipement."
            : "À traiter après que toutes les fondations soient vertes."
        }
        checks={slateChecks}
        ready={foundationsReady}
        dimmed={!foundationsReady}
      />
    </div>
  );
}

function CheckSection({
  title,
  subtitle,
  checks,
  ready,
  dimmed = false,
}: {
  title: string;
  subtitle: string;
  checks: CheckRow[];
  ready: boolean;
  dimmed?: boolean;
}) {
  return (
    <section className={cn("mb-6", dimmed && "opacity-60")}>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="cyber-display text-base">{title}</h2>
        <span
          className={cn(
            "text-[10px] uppercase tracking-[0.18em]",
            ready
              ? "text-emerald-300"
              : "text-[color:var(--color-cyber-muted)]",
          )}
        >
          {ready ? "groupe prêt" : "en attente"}
        </span>
      </div>
      <p className="mb-3 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
        {subtitle}
      </p>
      <ul className="flex flex-col gap-2">
        {checks.map((c) => (
          <CheckCard key={c.id} check={c} />
        ))}
      </ul>
    </section>
  );
}

/* ---------- subcomponents ---------- */

function CheckCard({ check }: { check: CheckRow }) {
  const style = STATUS_STYLES[check.status];
  return (
    <li
      className={cn(
        "cyber-panel flex items-start gap-4 p-4 transition-colors",
        style.border,
        style.bg,
      )}
    >
      <div className={cn("mt-0.5 shrink-0", style.icon)}>{style.iconNode}</div>
      <div className="flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="cyber-label text-xs">{check.label}</span>
          <span
            className={cn(
              "rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-[0.15em]",
              style.border,
              style.text,
            )}
          >
            {style.tag}
          </span>
        </div>
        <p className="mt-1 text-xs text-[color:var(--color-cyber-dim)]">
          {check.detail}
        </p>
      </div>
      <Link
        to={check.link}
        className="flex shrink-0 items-center gap-1 rounded border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
      >
        {check.linkLabel}
        <ChevronRight className="h-3 w-3" />
      </Link>
    </li>
  );
}

function SummaryBox({
  label,
  value,
  kind,
}: {
  label: string;
  value: number;
  kind: "ok" | "warn" | "missing";
}) {
  const colors = {
    ok: "text-emerald-300",
    warn: "text-amber-300",
    missing: "text-red-300",
  }[kind];
  return (
    <div className="flex flex-col items-center">
      <div className={cn("cyber-display text-3xl", colors)}>{value}</div>
      <div className="cyber-label text-[10px]">{label}</div>
    </div>
  );
}

/* ---------- helpers ---------- */

const STATUS_STYLES: Record<
  CheckStatus,
  { border: string; bg: string; text: string; icon: string; iconNode: React.ReactNode; tag: string }
> = {
  ok: {
    border: "border-emerald-500/40",
    bg: "bg-emerald-500/5",
    text: "text-emerald-300",
    icon: "text-emerald-300",
    iconNode: <CheckCircle2 className="h-5 w-5" />,
    tag: "OK",
  },
  warn: {
    border: "border-amber-500/40",
    bg: "bg-amber-500/5",
    text: "text-amber-300",
    icon: "text-amber-300",
    iconNode: <AlertTriangle className="h-5 w-5" />,
    tag: "Warn",
  },
  missing: {
    border: "border-red-500/40",
    bg: "bg-red-500/5",
    text: "text-red-300",
    icon: "text-red-300",
    iconNode: <CircleSlash className="h-5 w-5" />,
    tag: "Manquant",
  },
  error: {
    border: "border-red-500/40",
    bg: "bg-red-500/5",
    text: "text-red-300",
    icon: "text-red-300",
    iconNode: <XCircle className="h-5 w-5" />,
    tag: "Erreur",
  },
  loading: {
    border: "border-[color:var(--color-cyber-border)]",
    bg: "",
    text: "text-[color:var(--color-cyber-muted)]",
    icon: "text-[color:var(--color-cyber-muted)]",
    iconNode: <Loader2 className="h-5 w-5 animate-spin" />,
    tag: "…",
  },
};

function loading(
  id: string,
  label: string,
  link: string,
  linkLabel: string,
): CheckRow {
  return { id, label, status: "loading", detail: "Probe en cours…", link, linkLabel };
}

function errored(
  id: string,
  label: string,
  link: string,
  linkLabel: string,
  err: unknown,
): CheckRow {
  return {
    id,
    label,
    status: "error",
    detail: `Probe échouée : ${(err as Error)?.message ?? "erreur inconnue"}`,
    link,
    linkLabel,
  };
}
