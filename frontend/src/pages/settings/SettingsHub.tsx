import { Link } from "react-router-dom";
import {
  CheckCircle2,
  ChevronRight,
  Cog,
  Cpu,
  Key,
  Lock,
  MessageSquare,
  Shield,
  ShieldCheck,
} from "lucide-react";

import { useT } from "@/lib/i18n";

interface HubCard {
  to: string;
  icon: typeof Cog;
  titleKey: string;
  descKey: string;
}

const CARDS: HubCard[] = [
  {
    to: "/settings/setup-status",
    icon: CheckCircle2,
    titleKey: "settings.hub.setup_title",
    descKey: "settings.hub.setup_desc",
  },
  {
    to: "/settings/ssh-key",
    icon: Key,
    titleKey: "settings.hub.ssh_title",
    descKey: "settings.hub.ssh_desc",
  },
  {
    to: "/settings/controller-https",
    icon: Lock,
    titleKey: "settings.hub.https_title",
    descKey: "settings.hub.https_desc",
  },
  {
    to: "/settings/internal-ca",
    icon: ShieldCheck,
    titleKey: "settings.hub.ca_title",
    descKey: "settings.hub.ca_desc",
  },
  // `/settings/connectivity` est volontairement omis : les callback URLs
  // existent en store mais aucun consommateur ne les lit (les webhooks
  // Slate → controller ne sont pas implémentés). À ressortir quand on
  // attaquera les notifs anti-theft.
  {
    to: "/settings/tailnet-admin",
    icon: Shield,
    titleKey: "settings.hub.tailnet_title",
    descKey: "settings.hub.tailnet_desc",
  },
  {
    to: "/settings/communication",
    icon: MessageSquare,
    titleKey: "settings.hub.communication_title",
    descKey: "settings.hub.communication_desc",
  },
  {
    to: "/settings/agent",
    icon: Cpu,
    titleKey: "settings.hub.agent_title",
    descKey: "settings.hub.agent_desc",
  },
];

export default function SettingsHub() {
  const t = useT();
  const countKey =
    CARDS.length > 1
      ? "settings.subsection_count_plural"
      : "settings.subsection_count";

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Cog className="cyber-glow h-3 w-3" />
          {t("settings.subtitle")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("settings.title").toUpperCase()}
        >
          {t("settings.title").toUpperCase()}
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t(countKey, { n: CARDS.length })}
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {CARDS.map((c) => (
          <Link
            key={c.to}
            to={c.to}
            className="cyber-panel group flex flex-col gap-3 p-5 transition-all hover:border-[color:var(--color-cyber-accent)]"
          >
            <div className="flex items-center gap-2 text-[color:var(--color-cyber-accent)]">
              <c.icon className="h-5 w-5" />
              <h3 className="cyber-display cyber-glow text-base">
                {t(c.titleKey)}
              </h3>
              <ChevronRight className="ml-auto h-4 w-4 text-[color:var(--color-cyber-muted)] transition-transform group-hover:translate-x-1 group-hover:text-[color:var(--color-cyber-accent)]" />
            </div>
            <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
              {t(c.descKey)}
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
