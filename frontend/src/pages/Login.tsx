import { FormEvent, useState } from "react";
import { AxiosError } from "axios";
import { Lock, Terminal, User } from "lucide-react";

import { useLogin } from "@/hooks/useAuth";
import { useT } from "@/lib/i18n";

export default function Login() {
  const t = useT();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    login.mutate({ username, password });
  }

  const errorMessage =
    login.error instanceof AxiosError && login.error.response?.status === 401
      ? `[ ${t("login.err_unauthorized")} ]`
      : login.error
        ? `[ ${t("login.err_network")} ]`
        : null;

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="cyber-card cyber-card-accent w-full max-w-md p-10">
        <div className="mb-2 flex items-center gap-2">
          <Terminal className="cyber-glow h-4 w-4" />
          <span className="text-[10px] uppercase tracking-[0.35em] text-[color:var(--color-cyber-muted)]">
            {t("login.page_label")}
          </span>
        </div>
        <h1
          className="cyber-display cyber-glitch mb-8 text-3xl"
          data-text={t("login.page_title").toUpperCase()}
        >
          {t("login.page_title").toUpperCase()}
        </h1>

        <form onSubmit={onSubmit} className="space-y-5">
          <div>
            <span className="cyber-label mb-2 block">{t("login.username")}</span>
            <div className="relative">
              <User className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[color:var(--color-cyber-muted)]" />
              <input
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="cyber-input w-full py-2.5 pl-9 pr-3 text-sm"
              />
            </div>
          </div>

          <div>
            <span className="cyber-label mb-2 block">{t("login.password")}</span>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[color:var(--color-cyber-muted)]" />
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="cyber-input w-full py-2.5 pl-9 pr-3 text-sm"
              />
            </div>
          </div>

          {errorMessage && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              {errorMessage}
            </p>
          )}

          <button
            type="submit"
            disabled={login.isPending}
            className="cyber-button w-full px-4 py-3 text-sm"
          >
            {login.isPending
              ? t("login.connecting")
              : `${t("login.submit")} ▸`}
          </button>
        </form>

        <div className="mt-10 flex items-center justify-between">
          <div className="cyber-hatch h-2 w-16" />
          <p className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-muted)]">
            v0.1.0 · GL-BE10000
          </p>
        </div>
      </div>
    </main>
  );
}
