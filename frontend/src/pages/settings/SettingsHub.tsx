import { Link } from "react-router-dom";
import { ChevronRight, Cog, Cpu, Globe, Key, MessageSquare } from "lucide-react";

const cards = [
  {
    to: "/settings/ssh-key",
    icon: Key,
    title: "SSH Keypair",
    subtitle: "Auth clé-only sur le Slate · génération + déploiement",
  },
  {
    to: "/settings/connectivity",
    icon: Globe,
    title: "Connectivité Slate ↔ Controller",
    subtitle: "URLs Tailscale / LAN pour les callbacks du Slate vers nous",
  },
  {
    to: "/settings/communication",
    icon: MessageSquare,
    title: "Communication",
    subtitle: "Toggle messages écran + test à la demande",
  },
  {
    to: "/settings/agent",
    icon: Cpu,
    title: "Agent local",
    subtitle:
      "Déploie slate-ctrl sur le Slate · profils en JSON, apply offline, bouton physique",
  },
];

export default function SettingsHub() {
  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Cog className="cyber-glow h-3 w-3" />
          controller settings · configuration globale
        </div>
        <h1 className="cyber-display cyber-glitch text-4xl" data-text="SETTINGS">
          SETTINGS
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {cards.length} sous-section{cards.length > 1 ? "s" : ""}
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {cards.map((c) => (
          <Link
            key={c.to}
            to={c.to}
            className="cyber-panel group flex flex-col gap-3 p-5 transition-all hover:border-[color:var(--color-cyber-accent)]"
          >
            <div className="flex items-center gap-2 text-[color:var(--color-cyber-accent)]">
              <c.icon className="h-5 w-5" />
              <h3 className="cyber-display cyber-glow text-base">{c.title}</h3>
              <ChevronRight className="ml-auto h-4 w-4 text-[color:var(--color-cyber-muted)] transition-transform group-hover:translate-x-1 group-hover:text-[color:var(--color-cyber-accent)]" />
            </div>
            <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
              {c.subtitle}
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
