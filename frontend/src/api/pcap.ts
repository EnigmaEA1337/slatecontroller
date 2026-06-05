// PCAP capture API — LAN tcpdump sessions on the Slate (Phase 1).

import { api } from "./client";

export type PcapStatus =
  | "planned"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface PcapCapture {
  id: number;
  iface: string;
  duration_s: number;
  snaplen: number;
  filter_expr: string;
  status: PcapStatus;
  started_at: string;
  ended_at: string | null;
  bytes_captured: number;
  remote_path: string;
  error: string;
  label: string;
}

export interface PcapListResponse {
  captures: PcapCapture[];
  allowed_ifaces: string[];
  limits: {
    min_duration_s: number;
    max_duration_s: number;
    min_snaplen: number;
    max_snaplen: number;
    default_snaplen: number;
  };
}

export interface PcapStartBody {
  iface: string;
  duration_s: number;
  snaplen: number;
  filter_expr?: string;
  label?: string;
}

export async function listPcapCaptures(): Promise<PcapListResponse> {
  const { data } = await api.get<PcapListResponse>("/api/network/pcap");
  return data;
}

export async function startPcapCapture(
  body: PcapStartBody,
): Promise<PcapCapture> {
  const { data } = await api.post<PcapCapture>("/api/network/pcap", body);
  return data;
}

export async function getPcapCapture(id: number): Promise<PcapCapture> {
  const { data } = await api.get<PcapCapture>(`/api/network/pcap/${id}`);
  return data;
}

export async function stopPcapCapture(id: number): Promise<PcapCapture> {
  const { data } = await api.post<PcapCapture>(`/api/network/pcap/${id}/stop`);
  return data;
}

export async function deletePcapCapture(id: number): Promise<void> {
  await api.delete(`/api/network/pcap/${id}`);
}

/** Télécharge le pcap en streaming binaire via le client axios.
 *
 *  Pourquoi pas un simple `<a href>` : la navigation directe n'envoie
 *  pas l'en-tête `Authorization`, donc l'API répond 401 et le clic
 *  semble "ne rien faire". On passe par axios pour réutiliser
 *  l'intercepteur d'auth, puis on déclenche le téléchargement côté
 *  navigateur via un Object URL temporaire qu'on révoque ensuite.
 */
export async function downloadPcapCapture(
  id: number,
  filename?: string,
): Promise<void> {
  const { data } = await api.get<Blob>(`/api/network/pcap/${id}/download`, {
    responseType: "blob",
    // Le backend re-base64 le pcap via SSH (busybox sans `base64`), donc
    // la latence ressentie peut grimper sur les gros fichiers. 60 s
    // permet de couvrir un pcap de l'ordre de 50 MB sans erreur de
    // timeout client.
    timeout: 60_000,
  });
  const blob = new Blob([data], { type: "application/vnd.tcpdump.pcap" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename ?? `slate-pcap-${id}.pcap`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Laisser une frame au navigateur pour démarrer le download avant
  // qu'on révoque l'URL — sinon Safari/Firefox annulent parfois.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
