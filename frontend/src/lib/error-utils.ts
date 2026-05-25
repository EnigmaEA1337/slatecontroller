import { AxiosError } from "axios";

/**
 * Extract a user-friendly message from an error of unknown shape.
 *
 * Handles the three shapes the controller can return:
 *   - Plain string in `response.data.detail`  → returned as-is.
 *   - Pydantic validation array in `detail`   → joined as "field: msg · ...".
 *   - Anything else (network error, timeout)  → "[HTTP code] message".
 *
 * Originally duplicated in ~18 pages — single source of truth now. Add new
 * normalizers here rather than copy-pasting back into a page.
 */
export function errorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(
          (d: { loc?: string[]; msg?: string }) =>
            `${(d.loc ?? []).join(".")}: ${d.msg}`,
        )
        .join(" · ");
    }
    return `[HTTP ${err.response?.status ?? "?"}] ${err.message}`;
  }
  if (err instanceof Error) return err.message;
  return "Erreur inconnue";
}

/**
 * Format an ISO timestamp for compact display in tables and cards. Always
 * uses the user's locale. Returns `—` for null/undefined for consistency
 * with the "empty cell" pattern used across the UI.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
