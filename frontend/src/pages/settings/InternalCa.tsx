/**
 * Internal PKI sub-page — Trust Controller (RGS-compliant).
 *
 * Surfaces :
 *   - CA configuration form (Subject DN, key algo, hash, validity, profile preset)
 *   - Root CA status + init / regenerate / download
 *   - Issue a new leaf cert (CN + SANs)
 *   - Issued cert log (push to Slate, revoke, download)
 *   - Root CA install instructions (Mac, iOS, Linux)
 *
 * Defaults map to ANSSI RGS 2★ : ECDSA P-384, SHA-384, 10-year CA, 825-day
 * leaves, 128-bit serials. Operator picks 1★ / 3★ via the preset selector.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Plus,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { ClickableHost, ClickableHostList } from "@/components/ClickableHost";
import { useEffect, useState } from "react";
import {
  CAConfig,
  CertDetails,
  IssuanceSubject,
  IssuedCertSummary,
  KeyAlgorithm,
  SignatureHash,
  downloadLeafCert,
  downloadRootCert,
  getInternalCAStatus,
  getRootDetails,
  initCA,
  issueCert,
  listIssued,
  listProfiles,
  listSubjects,
  pushCertToSlate,
  regenerateCA,
  revokeCert,
  updateCAConfig,
} from "@/api/internalCa";
import { errorMessage } from "@/lib/error-utils";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const KEY_ALGORITHMS: { value: KeyAlgorithm; label: string; rgs: string }[] = [
  { value: "ecdsa-p256", label: "ECDSA P-256", rgs: "RGS 1★+" },
  { value: "ecdsa-p384", label: "ECDSA P-384", rgs: "RGS 2★" },
  { value: "ecdsa-p521", label: "ECDSA P-521", rgs: "RGS 3★" },
  { value: "rsa-2048", label: "RSA 2048", rgs: "RGS 1★" },
  { value: "rsa-3072", label: "RSA 3072", rgs: "RGS 2★" },
  { value: "rsa-4096", label: "RSA 4096", rgs: "RGS 3★" },
];

const HASHES: { value: SignatureHash; label: string }[] = [
  { value: "sha256", label: "SHA-256" },
  { value: "sha384", label: "SHA-384" },
  { value: "sha512", label: "SHA-512" },
];

export default function InternalCa() {
  const t = useT();
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["internal-ca", "status"],
    queryFn: getInternalCAStatus,
    refetchOnWindowFocus: false,
  });
  const profilesQ = useQuery({
    queryKey: ["internal-ca", "profiles"],
    queryFn: listProfiles,
    refetchOnWindowFocus: false,
  });
  const issuedQ = useQuery({
    queryKey: ["internal-ca", "issued"],
    queryFn: listIssued,
    refetchOnWindowFocus: false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["internal-ca"] });
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <ShieldCheck className="cyber-glow h-3 w-3" />
          {t("set_internal_ca.subtitle")}
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text={t("set_internal_ca.title").toUpperCase()}
        >
          {t("set_internal_ca.title").toUpperCase()}
        </h1>
        <p className="mt-2 max-w-2xl text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          {t("set_internal_ca.description")} Profils RGS 1★ / 2★ / 3★
          sélectionnables ; le défaut est RGS 2★ (ECDSA P-384, SHA-384, autorité
          valide 10 ans, feuille 825 jours).
        </p>
      </header>

      {status.isLoading && (
        <Loading message="lecture de l'état PKI…" />
      )}
      {status.isError && (
        <ErrorPanel message={errorMessage(status.error)} />
      )}

      {status.data && (
        <div className="flex flex-col gap-6">
          <RootCASection
            status={status.data}
            onChange={invalidate}
          />
          {status.data.initialized && <RootCADetailsSection />}
          <CAConfigSection
            initial={status.data.config}
            profiles={profilesQ.data ?? {}}
            initialized={status.data.initialized}
            onSaved={invalidate}
          />
          {status.data.initialized && (
            <>
              <IssueSection onIssued={invalidate} />
              <IssuedListSection
                items={issuedQ.data ?? []}
                slateSerial={status.data.slate_cert_serial_hex}
                onChange={invalidate}
              />
            </>
          )}
          <InstallInstructions />
        </div>
      )}
    </div>
  );
}

/* ---------- Root CA section ---------- */

function RootCASection({
  status,
  onChange,
}: {
  status: { initialized: boolean; config: CAConfig; issued_count: number; slate_cert_pushed_at: string | null };
  onChange: () => void;
}) {
  const initMut = useMutation({ mutationFn: initCA, onSuccess: onChange });
  const regenMut = useMutation({ mutationFn: regenerateCA, onSuccess: onChange });

  if (!status.initialized) {
    return (
      <section className="cyber-panel border-amber-500/40 bg-amber-500/5 p-5">
        <div className="cyber-label mb-2 text-[10px]">racine</div>
        <h2 className="cyber-display text-xl">Initialiser le CA racine</h2>
        <p className="mt-2 text-xs text-[color:var(--color-cyber-dim)]">
          Génère une autorité de certification locale avec la configuration
          ci-dessous. La clé privée du CA est écrite dans{" "}
          <code>./data/ca/rootCA.key</code> sur le controller — elle ne quitte
          jamais ce disque et n'est exposée par aucun endpoint. La clé publique
          (Root CA cert) est distribuable et destinée à être installée sur les
          équipements clients pour qu'ils fassent confiance aux certificats
          serveur émis par cette autorité.
        </p>
        <button
          onClick={() => initMut.mutate()}
          disabled={initMut.isPending}
          className="mt-4 rounded border border-emerald-500/40 bg-emerald-500/10 px-4 py-2 text-sm uppercase tracking-[0.18em] text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
        >
          {initMut.isPending ? "Génération…" : "Initialiser le Root CA"}
        </button>
        {initMut.isError && (
          <ErrorInline message={errorMessage(initMut.error)} />
        )}
      </section>
    );
  }

  return (
    <section className="cyber-panel border-emerald-500/40 bg-emerald-500/5 p-5">
      <div className="flex items-start gap-3">
        <CheckCircle2 className="mt-1 h-5 w-5 shrink-0 text-emerald-300" />
        <div className="flex-1">
          <div className="cyber-label text-[10px]">CA racine actif</div>
          <p className="mt-1 font-mono text-xs text-[color:var(--color-cyber-dim)]">
            CN={status.config.subject.common_name}
            {status.config.subject.organization
              ? `, O=${status.config.subject.organization}`
              : ""}
            {status.config.subject.organizational_unit
              ? `, OU=${status.config.subject.organizational_unit}`
              : ""}
            {status.config.subject.country ? `, C=${status.config.subject.country}` : ""}
          </p>
          <p className="mt-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
            algorithme : {status.config.key_algorithm.toUpperCase()} ·
            signature : {status.config.signature_hash.toUpperCase()} · validité
            CA : {status.config.validity.ca_days} jours · certs émis :{" "}
            {status.issued_count}
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <button
            onClick={() => {
              downloadRootCert().catch((e) =>
                alert("Échec téléchargement : " + errorMessage(e)),
              );
            }}
            className="rounded border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-center text-xs uppercase tracking-[0.15em] text-emerald-300 hover:bg-emerald-500/20"
          >
            <Download className="mr-1 inline h-3 w-3" />
            Root CA PEM
          </button>
          <button
            onClick={() => {
              if (
                confirm(
                  "Régénérer le Root CA invalide TOUS les certificats déjà émis (chaîne rompue). Tu devras réinstaller le nouveau Root CA sur chaque équipement client + ré-émettre + re-pousser le cert du Slate. Continuer ?",
                )
              )
                regenMut.mutate();
            }}
            disabled={regenMut.isPending}
            className="rounded border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs uppercase tracking-[0.15em] text-red-300 hover:bg-red-500/20 disabled:opacity-50"
            title="Wipe + rebuild — destructif"
          >
            <RotateCw className="mr-1 inline h-3 w-3" />
            {regenMut.isPending ? "Régén…" : "Régénérer"}
          </button>
        </div>
      </div>
      {(initMut.isError || regenMut.isError) && (
        <ErrorInline
          message={errorMessage(initMut.error || regenMut.error)}
        />
      )}
    </section>
  );
}

/* ---------- Root CA details (full X.509 view) ---------- */

function RootCADetailsSection() {
  const q = useQuery({
    queryKey: ["internal-ca", "details"],
    queryFn: getRootDetails,
    refetchOnWindowFocus: false,
  });
  const [showPem, setShowPem] = useState(false);

  if (q.isLoading) return <Loading message="parse du Root CA…" />;
  if (q.isError) return <ErrorPanel message={errorMessage(q.error)} />;
  const d: CertDetails | undefined = q.data;
  if (!d) return null;

  return (
    <section className="cyber-panel p-5">
      <div className="cyber-label mb-3 text-[10px]">
        détails X.509 du Root CA actif
      </div>
      <h2 className="cyber-display mb-4 text-xl">Tous les paramètres</h2>

      {/* Identity block */}
      <DetailGroup title="Identité">
        <DetailRow label="Version" value={d.version} mono />
        <DetailRow label="Numéro de série" value={d.serial_hex} mono />
        <DetailRow label="Subject (sujet)" value={d.subject} mono />
        <DetailRow
          label="Issuer (émetteur)"
          value={d.issuer + (d.is_self_signed ? "  · auto-signé" : "")}
          mono
        />
      </DetailGroup>

      {/* Validity */}
      <DetailGroup title="Période de validité">
        <DetailRow
          label="Pas avant"
          value={new Date(d.not_before).toLocaleString()}
        />
        <DetailRow
          label="Pas après"
          value={new Date(d.not_after).toLocaleString()}
        />
        <DetailRow
          label="Durée restante"
          value={`${Math.max(0, Math.round((new Date(d.not_after).getTime() - Date.now()) / 86400000))} jours`}
        />
      </DetailGroup>

      {/* Crypto */}
      <DetailGroup title="Cryptographie">
        <DetailRow label="Clé publique" value={d.public_key} mono />
        <DetailRow
          label="Algo de signature"
          value={`${d.signature_algorithm} (hash ${d.signature_hash})`}
          mono
        />
      </DetailGroup>

      {/* Fingerprints */}
      <DetailGroup title="Empreintes">
        <DetailRow label="SHA-256" value={d.fingerprint_sha256} mono small />
        <DetailRow label="SHA-1" value={d.fingerprint_sha1} mono small />
      </DetailGroup>

      {/* Extensions */}
      <DetailGroup title={`Extensions (${d.extensions.length})`}>
        <ul className="flex flex-col gap-1">
          {d.extensions.map((e, i) => (
            <li
              key={i}
              className="rounded border border-[color:var(--color-cyber-border)] bg-black/20 p-2 text-[11px]"
            >
              <div className="flex items-center gap-2">
                <span className="cyber-label text-[10px]">{e.name}</span>
                {e.critical && (
                  <span className="rounded border border-amber-500/50 px-1 text-[9px] uppercase tracking-[0.15em] text-amber-300">
                    critique
                  </span>
                )}
                <span className="font-mono text-[9px] text-[color:var(--color-cyber-muted)]">
                  OID {e.oid}
                </span>
              </div>
              <div className="mt-1 break-all font-mono text-[10px] text-[color:var(--color-cyber-dim)]">
                {e.value}
              </div>
            </li>
          ))}
        </ul>
      </DetailGroup>

      {/* PEM */}
      <DetailGroup title="PEM brut">
        <button
          onClick={() => setShowPem(!showPem)}
          className="rounded border border-[color:var(--color-cyber-border)] px-3 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          {showPem ? "Masquer" : "Afficher"} le PEM
        </button>
        {showPem && (
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-black/40 p-3 text-[10px] text-[color:var(--color-cyber-dim)]">
            {d.pem}
          </pre>
        )}
      </DetailGroup>
    </section>
  );
}

function DetailGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-5 last:mb-0">
      <div className="cyber-label mb-2 text-[10px] text-[color:var(--color-cyber-accent)]">
        {title}
      </div>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono,
  small,
}: {
  label: string;
  value: string;
  mono?: boolean;
  small?: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-1 text-xs md:grid-cols-[180px_1fr]">
      <span className="text-[color:var(--color-cyber-muted)]">{label}</span>
      <span
        className={cn(
          "break-all text-[color:var(--color-cyber-dim)]",
          mono && "font-mono",
          small && "text-[10px]",
        )}
      >
        {value}
      </span>
    </div>
  );
}

/* ---------- CA Config section (editable form) ---------- */

function CAConfigSection({
  initial,
  profiles,
  initialized,
  onSaved,
}: {
  initial: CAConfig;
  profiles: Record<string, CAConfig>;
  initialized: boolean;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<CAConfig>(initial);
  useEffect(() => {
    setDraft(initial);
  }, [initial]);
  const saveMut = useMutation({
    mutationFn: updateCAConfig,
    onSuccess: onSaved,
  });

  const update = (patch: Partial<CAConfig>) =>
    setDraft({ ...draft, ...patch });
  const updateSubject = (patch: Partial<CAConfig["subject"]>) =>
    setDraft({ ...draft, subject: { ...draft.subject, ...patch } });
  const updateValidity = (patch: Partial<CAConfig["validity"]>) =>
    setDraft({ ...draft, validity: { ...draft.validity, ...patch } });

  return (
    <section className="cyber-panel p-5">
      <div className="cyber-label mb-3 text-[10px]">configuration</div>
      <h2 className="cyber-display mb-4 text-xl">Paramètres du CA</h2>

      {/* Profile preset selector */}
      <div className="mb-5">
        <Label>profil RGS — applique des valeurs recommandées</Label>
        <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-3">
          {Object.entries(profiles).map(([key, profile]) => (
            <button
              key={key}
              onClick={() => setDraft(profile)}
              className={cn(
                "rounded border p-3 text-left text-xs transition-colors",
                "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-accent)]",
              )}
            >
              <div className="cyber-label text-[10px]">{profile.profile_label}</div>
              <div className="mt-1 font-mono text-[color:var(--color-cyber-dim)]">
                {profile.key_algorithm.toUpperCase()} /{" "}
                {profile.signature_hash.toUpperCase()}
              </div>
              <div className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                CA {profile.validity.ca_days}d · leaf {profile.validity.leaf_days}d
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Subject DN */}
      <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field
          label="CN — Common Name"
          value={draft.subject.common_name}
          onChange={(v) => updateSubject({ common_name: v })}
          placeholder="Trust Controller"
        />
        <Field
          label="O — Organization"
          value={draft.subject.organization ?? ""}
          onChange={(v) => updateSubject({ organization: v || null })}
          placeholder="Slate Controller PKI"
        />
        <Field
          label="OU — Organizational Unit"
          value={draft.subject.organizational_unit ?? ""}
          onChange={(v) => updateSubject({ organizational_unit: v || null })}
          placeholder="Internal Root CA"
        />
        <Field
          label="C — Country (ISO 3166)"
          value={draft.subject.country ?? ""}
          onChange={(v) => updateSubject({ country: v ? v.toUpperCase().slice(0, 2) : null })}
          placeholder="FR"
          maxLength={2}
        />
      </div>

      {/* Algorithms */}
      <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <Label>algorithme de clé</Label>
          <select
            value={draft.key_algorithm}
            onChange={(e) =>
              update({ key_algorithm: e.target.value as KeyAlgorithm })
            }
            className="mt-1 w-full rounded border border-[color:var(--color-cyber-border)] bg-black/40 px-3 py-1.5 font-mono text-xs text-[color:var(--color-cyber-dim)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
          >
            {KEY_ALGORITHMS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label} — {a.rgs}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label>hash de signature</Label>
          <select
            value={draft.signature_hash}
            onChange={(e) =>
              update({ signature_hash: e.target.value as SignatureHash })
            }
            className="mt-1 w-full rounded border border-[color:var(--color-cyber-border)] bg-black/40 px-3 py-1.5 font-mono text-xs text-[color:var(--color-cyber-dim)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
          >
            {HASHES.map((h) => (
              <option key={h.value} value={h.value}>
                {h.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Validity */}
      <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field
          label="validité CA (jours) — max 7305 (20 ans)"
          type="number"
          value={String(draft.validity.ca_days)}
          onChange={(v) => updateValidity({ ca_days: Math.max(365, parseInt(v) || 0) })}
        />
        <Field
          label="validité feuille (jours) — max 825 (CAB Forum)"
          type="number"
          value={String(draft.validity.leaf_days)}
          onChange={(v) =>
            updateValidity({ leaf_days: Math.min(825, Math.max(1, parseInt(v) || 0)) })
          }
        />
      </div>

      {/* PQ experimental toggle */}
      <div className="mb-5 rounded border border-amber-500/30 bg-amber-500/5 p-3">
        <label className="flex items-start gap-3 text-xs">
          <input
            type="checkbox"
            checked={draft.pq_hybrid_experimental}
            onChange={(e) =>
              update({ pq_hybrid_experimental: e.target.checked })
            }
            className="mt-0.5"
          />
          <div>
            <div className="cyber-label text-[10px] text-amber-300">
              mode hybride post-quantique (expérimental)
            </div>
            <p className="mt-1 text-[color:var(--color-cyber-dim)]">
              Toggle réservé pour activer la signature hybride ECDSA + ML-DSA
              (NIST FIPS 204) lorsque le module Phase 2 est livré.{" "}
              <strong>Non-fonctionnel aujourd'hui</strong> : aucun navigateur
              actuel ne fait confiance aux signatures ML-DSA, et le Slate
              (uhttpd + mbedTLS) ne négocie pas TLS post-quantique en
              handshake. Conserver désactivé jusqu'à ce que l'écosystème
              rattrape (horizon 2028).
            </p>
          </div>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => saveMut.mutate(draft)}
          disabled={saveMut.isPending}
          className="rounded border border-cyan-500/40 bg-cyan-500/10 px-4 py-1.5 text-xs uppercase tracking-[0.15em] text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-50"
        >
          {saveMut.isPending ? "Enregistrement…" : "Enregistrer la configuration"}
        </button>
        {initialized && (
          <span className="text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
            les modifications n'affectent que les futures émissions — le Root
            CA en cours reste inchangé jusqu'à régénération.
          </span>
        )}
      </div>
      {saveMut.isError && <ErrorInline message={errorMessage(saveMut.error)} />}
    </section>
  );
}

/* ---------- Issue new cert ---------- */

function IssueSection({ onIssued }: { onIssued: () => void }) {
  const subjectsQ = useQuery({
    queryKey: ["internal-ca", "subjects"],
    queryFn: listSubjects,
    refetchOnWindowFocus: false,
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [extraSanInput, setExtraSanInput] = useState("");
  const [extraSans, setExtraSans] = useState<string[]>([]);

  const subjects: IssuanceSubject[] = subjectsQ.data ?? [];
  const selected = subjects.find((s) => `${s.kind}:${s.id}` === selectedId);

  const issueMut = useMutation({
    mutationFn: () =>
      issueCert({
        subject_id: selectedId!,
        additional_sans: extraSans,
      }),
    onSuccess: () => {
      setExtraSans([]);
      setExtraSanInput("");
      onIssued();
    },
  });

  const addSan = () => {
    const v = extraSanInput.trim();
    if (!v || extraSans.includes(v) || selected?.suggested_sans.includes(v)) {
      setExtraSanInput("");
      return;
    }
    setExtraSans([...extraSans, v]);
    setExtraSanInput("");
  };

  return (
    <section className="cyber-panel p-5">
      <div className="cyber-label mb-3 text-[10px]">émission</div>
      <h2 className="cyber-display mb-3 text-xl">Émettre un certificat</h2>
      <p className="text-xs text-[color:var(--color-cyber-dim)]">
        L'émission est contrainte à un équipement enregistré : sélectionne dans
        la liste des devices adoptés ci-dessous, le Common Name et les SANs de
        base sont auto-renseignés à partir de la fiche équipement. Tu peux
        optionnellement ajouter des SANs supplémentaires (alias mDNS, IP
        statique alternative) mais pas modifier les baseline — politique
        anti-erreur, on n'émet pas pour des noms arbitraires.
      </p>

      {/* Subject selector */}
      <div className="mt-4">
        <Label>équipement cible</Label>
        {subjectsQ.isLoading ? (
          <div className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
            chargement…
          </div>
        ) : subjects.length === 0 ? (
          <div className="mt-1 rounded border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-300">
            <AlertTriangle className="mr-1 inline h-3 w-3" />
            Aucun équipement enregistré. Adopte au moins un device depuis
            l'onglet Devices avant d'émettre un certificat.
          </div>
        ) : (
          <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
            {subjects.map((s) => {
              const id = `${s.kind}:${s.id}`;
              const isSelected = selectedId === id;
              return (
                <button
                  key={id}
                  onClick={() => setSelectedId(id)}
                  className={cn(
                    "rounded border p-3 text-left text-xs transition-colors",
                    isSelected
                      ? "border-emerald-500/60 bg-emerald-500/10"
                      : "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-accent)]",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="cyber-label text-[10px]">{s.label}</span>
                    {isSelected && (
                      <CheckCircle2 className="h-3 w-3 text-emerald-300" />
                    )}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                    {id} · {s.notes}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-[color:var(--color-cyber-dim)]">
                    CN :{" "}
                    <ClickableHost value={s.suggested_common_name} />
                  </div>
                  <div className="mt-0.5 text-[10px]">
                    <span className="font-mono text-[color:var(--color-cyber-muted)]">
                      SANs :{" "}
                    </span>
                    <ClickableHostList items={s.suggested_sans} />
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Additional SANs (only relevant once a subject is picked) */}
      {selected && (
        <div className="mt-5">
          <Label>SANs additionnels (optionnel)</Label>
          <p className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
            Hostnames ou IPs supplémentaires à ajouter en plus des baseline de
            l'équipement. Utile pour un alias mDNS interne, un futur DNS perso,
            etc.
          </p>
          <div className="mt-2 flex gap-2">
            <input
              value={extraSanInput}
              onChange={(e) => setExtraSanInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addSan();
                }
              }}
              placeholder="ex: slate.tonlab.local"
              className="flex-1 rounded border border-[color:var(--color-cyber-border)] bg-black/40 px-3 py-1.5 font-mono text-xs text-[color:var(--color-cyber-dim)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
            />
            <button
              onClick={addSan}
              disabled={!extraSanInput.trim()}
              className="rounded border border-[color:var(--color-cyber-border)] px-3 py-1.5 text-xs text-[color:var(--color-cyber-dim)] hover:border-[color:var(--color-cyber-accent)] disabled:opacity-50"
            >
              <Plus className="h-3 w-3" />
            </button>
          </div>
          {extraSans.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-2">
              {extraSans.map((s) => (
                <li
                  key={s}
                  className="flex items-center gap-1.5 rounded border border-[color:var(--color-cyber-border)] bg-black/30 px-2 py-1 font-mono text-[11px] text-[color:var(--color-cyber-dim)]"
                >
                  {s}
                  <button
                    onClick={() => setExtraSans(extraSans.filter((x) => x !== s))}
                    className="text-[color:var(--color-cyber-muted)] hover:text-red-300"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="mt-5 flex items-center gap-3">
        <button
          onClick={() => issueMut.mutate()}
          disabled={issueMut.isPending || !selectedId}
          className="rounded border border-emerald-500/40 bg-emerald-500/10 px-4 py-1.5 text-xs uppercase tracking-[0.15em] text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
        >
          {issueMut.isPending ? "Signature…" : "Émettre le certificat"}
        </button>
        {issueMut.data && (
          <span className="text-[11px] text-emerald-300">
            émis : {issueMut.data.serial_hex.slice(0, 16)}…
          </span>
        )}
      </div>
      {issueMut.isError && <ErrorInline message={errorMessage(issueMut.error)} />}
    </section>
  );
}

/* ---------- Issued list ---------- */

function IssuedListSection({
  items,
  slateSerial,
  onChange,
}: {
  items: IssuedCertSummary[];
  slateSerial: string | null;
  onChange: () => void;
}) {
  return (
    <section className="cyber-panel p-5">
      <div className="cyber-label mb-3 text-[10px]">
        journal d'émission · {items.length} certificat{items.length > 1 ? "s" : ""}
      </div>
      <h2 className="cyber-display mb-4 text-xl">Certificats émis</h2>
      {items.length === 0 ? (
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          Aucun certificat émis pour l'instant.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((c) => (
            <IssuedRow
              key={c.serial_hex}
              cert={c}
              isSlate={c.serial_hex === slateSerial}
              onChange={onChange}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function IssuedRow({
  cert,
  isSlate,
  onChange,
}: {
  cert: IssuedCertSummary;
  isSlate: boolean;
  onChange: () => void;
}) {
  const pushMut = useMutation({
    mutationFn: () => pushCertToSlate(cert.serial_hex),
    onSuccess: onChange,
  });
  const revokeMut = useMutation({
    mutationFn: () => revokeCert(cert.serial_hex),
    onSuccess: onChange,
  });
  const revoked = !!cert.revoked_at;
  const expired = new Date(cert.not_after) < new Date();

  return (
    <div
      className={cn(
        "rounded border p-3 text-xs",
        revoked
          ? "border-red-500/40 bg-red-500/5"
          : isSlate
            ? "border-emerald-500/40 bg-emerald-500/5"
            : "border-[color:var(--color-cyber-border)]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="cyber-label">{cert.common_name}</span>
            {isSlate && (
              <span className="rounded border border-emerald-500/50 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.15em] text-emerald-300">
                cert slate actif
              </span>
            )}
            {revoked && (
              <span className="rounded border border-red-500/50 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.15em] text-red-300">
                révoqué
              </span>
            )}
            {expired && !revoked && (
              <span className="rounded border border-amber-500/50 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.15em] text-amber-300">
                expiré
              </span>
            )}
          </div>
          <p className="mt-1 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
            serial : {cert.serial_hex}
          </p>
          <div className="mt-0.5 text-[10px]">
            <span className="font-mono text-[color:var(--color-cyber-muted)]">
              SANs :{" "}
            </span>
            <ClickableHostList items={cert.sans} />
          </div>
          <p className="mt-0.5 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
            émis : {new Date(cert.issued_at).toLocaleString()} · expire :{" "}
            {new Date(cert.not_after).toLocaleDateString()}
            {revoked &&
              ` · révoqué : ${new Date(cert.revoked_at!).toLocaleString()}`}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={() => {
              downloadLeafCert(cert.serial_hex).catch((e) =>
                alert("Échec téléchargement : " + errorMessage(e)),
              );
            }}
            className="rounded border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <Download className="mr-1 inline h-3 w-3" />
            PEM
          </button>
          {!revoked && !expired && (
            <button
              onClick={() => pushMut.mutate()}
              disabled={pushMut.isPending}
              className="rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
            >
              <Upload className="mr-1 inline h-3 w-3" />
              {pushMut.isPending ? "Push…" : "Pousser au Slate"}
            </button>
          )}
          {!revoked && (
            <button
              onClick={() => {
                if (
                  confirm(
                    `Marquer le certificat ${cert.serial_hex.slice(0, 16)}… comme révoqué ? Réversible uniquement par ré-émission.`,
                  )
                )
                  revokeMut.mutate();
              }}
              disabled={revokeMut.isPending}
              className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-red-300 hover:bg-red-500/20 disabled:opacity-50"
            >
              <XCircle className="mr-1 inline h-3 w-3" />
              {revokeMut.isPending ? "…" : "Révoquer"}
            </button>
          )}
        </div>
      </div>
      {(pushMut.isError || revokeMut.isError) && (
        <ErrorInline
          message={errorMessage(pushMut.error || revokeMut.error)}
        />
      )}
    </div>
  );
}

/* ---------- Install instructions ---------- */

function InstallInstructions() {
  return (
    <section className="cyber-panel p-5">
      <div className="cyber-label mb-3 text-[10px]">
        installation du Root CA sur les équipements clients
      </div>
      <h2 className="cyber-display mb-3 text-lg">Procédure one-time par device</h2>
      <p className="text-xs text-[color:var(--color-cyber-dim)]">
        Le Root CA doit être ajouté au magasin de confiance de chaque équipement
        client (poste opérateur, smartphone). Une fois installé, le système
        d'exploitation valide automatiquement tous les certificats émis par
        cette autorité — plus aucun avertissement TLS.
      </p>
      <div className="mt-4 grid grid-cols-1 gap-4 text-xs text-[color:var(--color-cyber-dim)] md:grid-cols-3">
        <div>
          <div className="cyber-label mb-1 text-[10px]">macOS</div>
          <ol className="list-decimal space-y-1 pl-4">
            <li>Télécharger le Root CA (bouton ci-dessus)</li>
            <li>Double-cliquer le fichier .pem</li>
            <li>
              Trousseau s'ouvre → ajouter au trousseau <code>login</code>
            </li>
            <li>
              Localiser le certificat → double-cliquer → section "Faire
              confiance" → "Toujours faire confiance"
            </li>
          </ol>
        </div>
        <div>
          <div className="cyber-label mb-1 text-[10px]">iOS / iPadOS</div>
          <ol className="list-decimal space-y-1 pl-4">
            <li>Télécharger via Safari, autoriser le profil</li>
            <li>
              Réglages → Général → VPN et gestion d'appareils → <strong>Profil téléchargé</strong>
            </li>
            <li>Installer (saisir le code de l'appareil)</li>
            <li>
              Réglages → Général → À propos → Réglages de confiance des
              certificats → activer le toggle pour le CA installé
            </li>
          </ol>
        </div>
        <div>
          <div className="cyber-label mb-1 text-[10px]">Linux (Debian / Ubuntu)</div>
          <pre className="overflow-x-auto rounded bg-black/40 p-2 text-[10px]">
            {`sudo cp trust-controller-root-ca.pem \\
  /usr/local/share/ca-certificates/\\
  trust-controller-root-ca.crt
sudo update-ca-certificates`}
          </pre>
          <p className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
            Firefox utilise son propre magasin — ajouter via Paramètres → Vie
            privée et sécurité → Certificats → Voir les certificats →
            Importer.
          </p>
        </div>
      </div>
    </section>
  );
}

/* ---------- Small UI primitives ---------- */

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  maxLength,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  maxLength?: number;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
        className="mt-1 w-full rounded border border-[color:var(--color-cyber-border)] bg-black/40 px-3 py-1.5 font-mono text-xs text-[color:var(--color-cyber-dim)] placeholder:text-[color:var(--color-cyber-muted)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
      />
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="cyber-label block text-[10px]">{children}</span>
  );
}

function Loading({ message }: { message: string }) {
  return (
    <div className="cyber-panel flex items-center gap-3 p-4 text-xs text-[color:var(--color-cyber-muted)]">
      <RefreshCw className="h-4 w-4 animate-spin" /> {message}
    </div>
  );
}

function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="cyber-panel border-red-500/40 bg-red-500/5 p-4 text-xs text-red-300">
      <AlertTriangle className="mr-1 inline h-4 w-4" />
      {message}
    </div>
  );
}

function ErrorInline({ message }: { message: string }) {
  return (
    <div className="mt-3 rounded border border-red-500/40 bg-red-500/5 p-2 text-[11px] text-red-300">
      <AlertTriangle className="mr-1 inline h-3 w-3" />
      {message}
    </div>
  );
}
