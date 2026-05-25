import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Cog,
  Copy,
  Download,
  Fingerprint,
  Key,
  Lock,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from "lucide-react";
import {
  deploySshKeypair,
  exportSshPrivateKey,
  generateSshKeypair,
  getSshKeypairStatus,
  revokeSshKeypair,
} from "@/api/settings";
import { cn } from "@/lib/utils";
import { errorMessage, formatDate } from "@/lib/error-utils";



function SshKeypairSection() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["settings", "ssh-keypair"],
    queryFn: getSshKeypairStatus,
  });

  const [copied, setCopied] = useState(false);
  const [confirmGenerate, setConfirmGenerate] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [disablePasswordAuth, setDisablePasswordAuth] = useState(false);
  const [confirmDisablePw, setConfirmDisablePw] = useState(false);
  const [confirmExport, setConfirmExport] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["settings", "ssh-keypair"] });

  const generate = useMutation({
    mutationFn: generateSshKeypair,
    onSuccess: () => {
      setConfirmGenerate(false);
      refresh();
    },
  });

  const deploy = useMutation({
    mutationFn: () => deploySshKeypair({ disable_password_auth: disablePasswordAuth }),
    onSuccess: () => {
      setConfirmDisablePw(false);
      refresh();
      // Also re-fetch hardening — a successful deploy moves the SSH key-only check.
      queryClient.invalidateQueries({ queryKey: ["slate-hardening"] });
    },
  });

  const revoke = useMutation({
    mutationFn: revokeSshKeypair,
    onSuccess: () => {
      setConfirmRevoke(false);
      refresh();
      queryClient.invalidateQueries({ queryKey: ["slate-hardening"] });
    },
  });

  const data = status.data;
  const hasKey = data?.generated ?? false;
  const deployed = data?.deployed_to_slate ?? false;
  const authMode = data?.auth_mode ?? "password";

  function copyPub() {
    if (!data?.public_openssh) return;
    void navigator.clipboard.writeText(data.public_openssh).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  async function downloadPrivateKey() {
    setExporting(true);
    setExportErr(null);
    try {
      const blob = await exportSshPrivateKey();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "slate-id_ed25519";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setConfirmExport(false);
    } catch (err) {
      setExportErr(errorMessage(err));
    } finally {
      setExporting(false);
    }
  }

  return (
    <section className="cyber-card p-6">
      <header className="mb-4 flex items-center gap-3">
        <div className="cyber-glow flex h-10 w-10 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10">
          <Key className="h-5 w-5" />
        </div>
        <div>
          <h2 className="cyber-display cyber-glow text-lg">SSH KEYPAIR</h2>
          <p className="mt-0.5 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            authentification backend → slate (Ed25519)
          </p>
        </div>
        <div className="ml-auto">
          {authMode === "key" ? (
            <span className="cyber-chip cyber-chip-ok inline-flex items-center gap-1.5">
              <Lock className="h-3 w-3" />
              key auth
            </span>
          ) : (
            <span className="cyber-chip inline-flex items-center gap-1.5">
              <Key className="h-3 w-3" />
              password auth
            </span>
          )}
        </div>
      </header>

      <div className="cyber-hatch mb-4 h-px w-full" />

      {status.isLoading && <p className="cyber-label cyber-cursor">chargement</p>}

      {status.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(status.error)}
        </p>
      )}

      {data && (
        <div className="space-y-5">
          {/* Status grid */}
          <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
              <div className="cyber-label mb-1">keypair généré</div>
              <div className="cyber-glow font-mono text-sm font-extrabold">
                {hasKey ? "OUI" : "non"}
              </div>
              {data.created_at && (
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
                  {formatDate(data.created_at)}
                </div>
              )}
            </div>
            <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
              <div className="cyber-label mb-1">déployé sur slate</div>
              <div
                className={cn(
                  "font-mono text-sm font-extrabold",
                  deployed ? "cyber-glow" : "text-[color:var(--color-cyber-muted)]",
                )}
              >
                {deployed ? "OUI" : "non"}
              </div>
              {data.deployed_at && (
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
                  {formatDate(data.deployed_at)}
                </div>
              )}
            </div>
          </div>

          {/* Fingerprint + pubkey */}
          {hasKey && data.fingerprint_sha256 && (
            <div>
              <div className="cyber-label mb-1.5 flex items-center gap-2">
                <Fingerprint className="h-3 w-3" />
                fingerprint
              </div>
              <code className="block break-all rounded-none border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)] px-3 py-2 font-mono text-xs">
                {data.fingerprint_sha256}
              </code>
            </div>
          )}

          {hasKey && data.public_openssh && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="cyber-label">public key (OpenSSH)</span>
                <button
                  type="button"
                  onClick={copyPub}
                  className="inline-flex items-center gap-1.5 border border-transparent px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                >
                  {copied ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                  {copied ? "copié" : "copier"}
                </button>
              </div>
              <code className="block break-all rounded-none border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)] px-3 py-2 font-mono text-[11px] leading-relaxed">
                {data.public_openssh}
              </code>
            </div>
          )}

          {/* Actions */}
          <div className="space-y-3">
            {/* Generate */}
            {!hasKey && (
              <button
                type="button"
                disabled={generate.isPending}
                onClick={() => generate.mutate()}
                className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs disabled:opacity-50"
              >
                <RefreshCw
                  className={cn("h-3.5 w-3.5", generate.isPending && "animate-spin")}
                />
                Générer keypair Ed25519
              </button>
            )}

            {hasKey && !confirmGenerate && (
              <button
                type="button"
                onClick={() => setConfirmGenerate(true)}
                className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] px-4 py-2.5 text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] transition hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Regénérer keypair
              </button>
            )}

            {confirmGenerate && (
              <div className="border border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 p-4 text-xs">
                <div className="mb-2 flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-cyber-warn)]" />
                  <div>
                    Regénérer écrasera la clé existante. Tu devras la <strong>redéployer</strong> sur le Slate, sinon l'auth key échouera et le backend basculera en password.
                  </div>
                </div>
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    disabled={generate.isPending}
                    onClick={() => generate.mutate()}
                    className="cyber-button px-3 py-1.5 text-[11px] disabled:opacity-50"
                  >
                    {generate.isPending ? "génération…" : "confirmer"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmGenerate(false)}
                    className="border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
                  >
                    annuler
                  </button>
                </div>
              </div>
            )}

            {/* Deploy */}
            {hasKey && !deployed && (
              <button
                type="button"
                disabled={deploy.isPending}
                onClick={() => deploy.mutate()}
                className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs disabled:opacity-50"
              >
                <UploadCloud className="h-3.5 w-3.5" />
                {deploy.isPending ? "déploiement…" : "Pousser sur le Slate + switch key auth"}
              </button>
            )}

            {/* Redeploy (idempotent) */}
            {hasKey && deployed && (
              <details className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3 text-xs">
                <summary className="cursor-pointer select-none text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]">
                  Re-déployer / appliquer key auth
                </summary>
                <div className="mt-3 space-y-2">
                  <p className="text-[11px] text-[color:var(--color-cyber-dim)]">
                    Re-pousse la clé publique (idempotent — la ligne n'est ajoutée que si absente) et bascule la connexion SSH backend vers la clé privée.
                  </p>
                  <button
                    type="button"
                    disabled={deploy.isPending}
                    onClick={() => deploy.mutate()}
                    className="cyber-button px-3 py-1.5 text-[11px] disabled:opacity-50"
                  >
                    {deploy.isPending ? "déploiement…" : "re-déployer"}
                  </button>
                </div>
              </details>
            )}

            {/* Disable password auth (high-stakes) */}
            {hasKey && deployed && authMode === "key" && (
              <div className="border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] p-4">
                <div className="cyber-label mb-2 flex items-center gap-2">
                  <ShieldCheck className="h-3 w-3" />
                  durcir : couper l'auth par mot de passe sur le Slate
                </div>
                <p className="mb-3 text-[11px] text-[color:var(--color-cyber-dim)]">
                  Désactive <code className="font-mono">dropbear.PasswordAuth</code> sur le Slate. SSH ne fonctionnera plus qu'avec la clé. <strong>Lockout possible</strong> si tu perds la clé privée — sauvegarde-la d'abord.
                </p>
                {!confirmDisablePw ? (
                  <button
                    type="button"
                    onClick={() => {
                      setDisablePasswordAuth(true);
                      setConfirmDisablePw(true);
                    }}
                    className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-warn)] hover:bg-[color:var(--color-cyber-warn)]/15"
                  >
                    <Lock className="h-3 w-3" />
                    désactiver password auth
                  </button>
                ) : (
                  <div className="space-y-2">
                    <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-warn)]">
                      ⚠ confirmation requise
                    </p>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={deploy.isPending}
                        onClick={() => deploy.mutate()}
                        className="cyber-button px-3 py-1.5 text-[11px] disabled:opacity-50"
                      >
                        {deploy.isPending ? "application…" : "confirmer"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setConfirmDisablePw(false);
                          setDisablePasswordAuth(false);
                        }}
                        className="border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
                      >
                        annuler
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Export private key (backup, sensitive) */}
            {hasKey && (
              <div>
                {!confirmExport ? (
                  <button
                    type="button"
                    onClick={() => {
                      setExportErr(null);
                      setConfirmExport(true);
                    }}
                    className="inline-flex items-center gap-2 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                  >
                    <Download className="h-3 w-3" />
                    Exporter clé privée (backup)
                  </button>
                ) : (
                  <div className="border border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 p-4 text-xs">
                    <div className="mb-2 flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-cyber-warn)]" />
                      <div>
                        Télécharge la clé privée en clair (<code className="font-mono">slate-id_ed25519</code>). Stocke-la dans un coffre-fort (KeePass, 1Password, USB chiffrée). <strong>Quiconque possède ce fichier peut SSH sur ton Slate en root.</strong>
                        <br />
                        <span className="text-[color:var(--color-cyber-dim)]">
                          L'export est audit-loggé côté backend.
                        </span>
                      </div>
                    </div>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        disabled={exporting}
                        onClick={() => void downloadPrivateKey()}
                        className="cyber-button px-3 py-1.5 text-[11px] disabled:opacity-50"
                      >
                        {exporting ? "téléchargement…" : "télécharger maintenant"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setConfirmExport(false);
                          setExportErr(null);
                        }}
                        className="border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
                      >
                        annuler
                      </button>
                    </div>
                    {exportErr && (
                      <p className="mt-2 text-[11px] text-[color:var(--color-cyber-accent)]">
                        {exportErr}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Revoke */}
            {hasKey && (
              <div>
                {!confirmRevoke ? (
                  <button
                    type="button"
                    onClick={() => setConfirmRevoke(true)}
                    className="inline-flex items-center gap-2 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                  >
                    <Trash2 className="h-3 w-3" />
                    Révoquer (revenir au password)
                  </button>
                ) : (
                  <div className="border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8 p-3 text-xs">
                    <div className="mb-2 flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                      <div>
                        Supprime la clé privée du backend et bascule la connexion SSH sur password.
                        <br />
                        <span className="text-[color:var(--color-cyber-dim)]">
                          Note : la clé publique reste dans <code className="font-mono">/etc/dropbear/authorized_keys</code> sur le Slate — à retirer manuellement.
                        </span>
                      </div>
                    </div>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        disabled={revoke.isPending}
                        onClick={() => revoke.mutate()}
                        className="cyber-button px-3 py-1.5 text-[11px] disabled:opacity-50"
                      >
                        {revoke.isPending ? "révocation…" : "confirmer la révocation"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmRevoke(false)}
                        className="border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
                      >
                        annuler
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Mutation results / errors */}
          {generate.error && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              {errorMessage(generate.error)}
            </p>
          )}
          {deploy.error && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              {errorMessage(deploy.error)}
            </p>
          )}
          {deploy.data && (
            <p className="border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8 px-3 py-2 text-xs">
              <CheckCircle2 className="mr-1.5 inline h-3 w-3" />
              {deploy.data.note}
            </p>
          )}
          {revoke.error && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              {errorMessage(revoke.error)}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default function SshKey() {
  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Cog className="cyber-glow h-3 w-3" />
          settings · ssh keypair
        </div>
        <h1 className="cyber-display cyber-glitch text-4xl" data-text="SSH KEY">
          SSH KEY
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          Auth clé-only sur le Slate · plus de mot de passe en clair
        </p>
      </header>

      <SshKeypairSection />
    </div>
  );
}
