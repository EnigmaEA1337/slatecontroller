import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertOctagon, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Optional custom fallback. Receives the error + a `reset` function. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Top-level error boundary. Catches React render-phase exceptions in any
 * descendant and shows a styled fallback instead of a white screen.
 *
 * Scope: doesn't catch async/event-handler errors (those need try/catch
 * locally) — only render-time and lifecycle errors. Pair with route-level
 * boundaries if a section grows enough to deserve its own fallback.
 *
 * Implementation note: class component required — React doesn't expose
 * `componentDidCatch` as a hook. This is the one place we still use a
 * class in 2026.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-8">
        <div className="cyber-card max-w-lg p-6">
          <div className="mb-4 flex items-center gap-3">
            <div className="cyber-glow flex h-10 w-10 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10">
              <AlertOctagon className="h-5 w-5 text-[color:var(--color-cyber-accent)]" />
            </div>
            <div>
              <h2 className="cyber-display cyber-glow text-lg">Erreur d'affichage</h2>
              <p className="text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
                un composant a planté en cours de rendu
              </p>
            </div>
          </div>
          <p className="mb-3 font-mono text-xs text-[color:var(--color-cyber-fg)]">
            {error.message || "Erreur inconnue"}
          </p>
          <details className="mb-4 cursor-pointer text-[10px] text-[color:var(--color-cyber-muted)]">
            <summary className="cursor-pointer uppercase tracking-[0.15em]">
              stack trace
            </summary>
            <pre className="mt-2 max-h-48 overflow-auto rounded border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)] p-2 font-mono">
              {error.stack || "(pas de stack)"}
            </pre>
          </details>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={this.reset}
              className="cyber-button inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px]"
            >
              <RefreshCw className="h-3 w-3" />
              réessayer
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
            >
              recharger la page
            </button>
          </div>
        </div>
      </div>
    );
  }
}
