import { api } from "./client";

export type KeyAlgorithm =
  | "ecdsa-p256"
  | "ecdsa-p384"
  | "ecdsa-p521"
  | "rsa-2048"
  | "rsa-3072"
  | "rsa-4096";

export type SignatureHash = "sha256" | "sha384" | "sha512";

export interface SubjectDN {
  common_name: string;
  organization?: string | null;
  organizational_unit?: string | null;
  country?: string | null;
  state?: string | null;
  locality?: string | null;
}

export interface CAValidity {
  ca_days: number;
  leaf_days: number;
}

export interface CAConfig {
  profile_label: string;
  subject: SubjectDN;
  key_algorithm: KeyAlgorithm;
  signature_hash: SignatureHash;
  validity: CAValidity;
  leaf_subject_template?: SubjectDN | null;
  pq_hybrid_experimental: boolean;
}

export interface IssuedCertSummary {
  serial_hex: string;
  common_name: string;
  sans: string[];
  issued_at: string;
  not_after: string;
  revoked_at: string | null;
  is_slate_cert: boolean;
}

export interface InternalCAStatus {
  initialized: boolean;
  config: CAConfig;
  profile_keys: string[];
  issued_count: number;
  slate_cert_serial_hex: string | null;
  slate_cert_pushed_at: string | null;
}

export interface WriteResponse {
  ok: boolean;
  message: string;
  serial_hex: string | null;
}

const BASE = "/api/settings/internal-ca";

export const getInternalCAStatus = async (): Promise<InternalCAStatus> =>
  (await api.get<InternalCAStatus>(BASE)).data;

export const getCAConfig = async (): Promise<CAConfig> =>
  (await api.get<CAConfig>(`${BASE}/config`)).data;

export const updateCAConfig = async (cfg: CAConfig): Promise<CAConfig> =>
  (await api.put<CAConfig>(`${BASE}/config`, cfg)).data;

export const listProfiles = async (): Promise<Record<string, CAConfig>> =>
  (await api.get<Record<string, CAConfig>>(`${BASE}/profiles`)).data;

export const initCA = async (): Promise<InternalCAStatus> =>
  (await api.post<InternalCAStatus>(`${BASE}/init`, null, { timeout: 30_000 })).data;

export const regenerateCA = async (): Promise<InternalCAStatus> =>
  (
    await api.post<InternalCAStatus>(
      `${BASE}/regenerate`,
      { confirm: true },
      { timeout: 30_000 },
    )
  ).data;

export interface CertExtension {
  oid: string;
  name: string;
  critical: boolean;
  value: string;
}

export interface CertDetails {
  version: string;
  serial_hex: string;
  serial_colon_hex: string;
  subject: string;
  issuer: string;
  is_self_signed: boolean;
  not_before: string;
  not_after: string;
  public_key: string;
  signature_algorithm: string;
  signature_hash: string;
  fingerprint_sha256: string;
  fingerprint_sha1: string;
  extensions: CertExtension[];
  pem: string;
}

export const getRootDetails = async (): Promise<CertDetails> =>
  (await api.get<CertDetails>(`${BASE}/details`)).data;

export const listIssued = async (): Promise<IssuedCertSummary[]> =>
  (await api.get<IssuedCertSummary[]>(`${BASE}/issued`)).data;

export interface IssuanceSubject {
  kind: string;
  id: string;
  label: string;
  suggested_common_name: string;
  suggested_sans: string[];
  notes: string;
}

export const listSubjects = async (): Promise<IssuanceSubject[]> =>
  (await api.get<IssuanceSubject[]>(`${BASE}/subjects`)).data;

export interface IssueRequest {
  subject_id: string;
  additional_sans: string[];
}

export const issueCert = async (req: IssueRequest): Promise<IssuedCertSummary> =>
  (await api.post<IssuedCertSummary>(`${BASE}/issued`, req, { timeout: 30_000 })).data;

export const revokeCert = async (serialHex: string): Promise<IssuedCertSummary> =>
  (await api.post<IssuedCertSummary>(`${BASE}/issued/${serialHex}/revoke`)).data;

export const pushCertToSlate = async (serialHex: string): Promise<WriteResponse> =>
  (
    await api.post<WriteResponse>(
      `${BASE}/issued/${serialHex}/push`,
      null,
      { timeout: 30_000 },
    )
  ).data;

/** Trigger a browser download via authenticated axios call → blob → temp <a>.
 *
 * A plain `<a href download>` cannot do this : it issues an unauthenticated
 * GET (no Authorization header), so endpoints behind `get_current_user`
 * return 401 and the browser silently shows the download as failed. We
 * fetch the PEM with the same axios instance the rest of the app uses
 * (which has the JWT interceptor), then synthesize an object URL.
 */
async function authenticatedDownload(
  path: string,
  filename: string,
): Promise<void> {
  const response = await api.get(path, { responseType: "blob" });
  const blob = response.data as Blob;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Free the object URL on next tick — Chrome holds it open briefly while
  // the download starts.
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

export const downloadRootCert = () =>
  authenticatedDownload(`${BASE}/root-cert`, "trust-controller-root-ca.pem");

export const downloadLeafCert = (serialHex: string) =>
  authenticatedDownload(
    `${BASE}/issued/${serialHex}/cert`,
    `cert-${serialHex.slice(0, 16)}.pem`,
  );
