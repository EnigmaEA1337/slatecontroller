"""Scanner orchestrator: collect SBOM, run sources, dedup, enrich, persist."""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from app.security.enrichers.cve2capec import Cve2CapecEnricher
from app.security.exploit_enricher import ExploitEnricher
from app.security.inventory import collect_inventory
from app.security.models import AttackPath, Finding, Inventory
from app.security.sources.base import VulnSource
from app.security.store import SecurityStore
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}


def _merge_cross_source(findings: list[Finding]) -> list[Finding]:
    """Dedup findings sharing (cve_id, package_name) across sources.

    Keeps the highest-severity / most-complete record but joins source names
    so the UI shows the corroborating set.
    """
    best: dict[tuple[str, str], Finding] = {}
    sources_seen: dict[tuple[str, str], set[str]] = {}
    for f in findings:
        key = (f.cve_id, f.package_name)
        sources_seen.setdefault(key, set()).add(f.source)
        prev = best.get(key)
        if prev is None:
            best[key] = f
            continue
        keep_new = (
            _SEVERITY_RANK[f.severity] > _SEVERITY_RANK[prev.severity]
            or (prev.cvss_score is None and f.cvss_score is not None)
            or (not prev.fixed_in and bool(f.fixed_in))
        )
        if keep_new:
            best[key] = f
    # Stash extra source ids into aliases so the UI can show them
    out: list[Finding] = []
    for key, f in best.items():
        extras = sorted(sources_seen[key] - {f.source})
        if extras:
            f = f.model_copy(update={"aliases": list({*f.aliases, *(f"source:{e}" for e in extras)})})
        out.append(f)
    return out


class SecurityScanner:
    """Coordinates inventory collection + sources + post-match enrichment."""

    def __init__(
        self,
        sources: Sequence[VulnSource],
        enricher: Cve2CapecEnricher | None = None,
        exploit_enricher: ExploitEnricher | None = None,
    ) -> None:
        self._sources = list(sources)
        self._enricher = enricher
        self._exploit_enricher = exploit_enricher

    async def run(
        self,
        *,
        ssh: SlateSSH,
        store: SecurityStore,
        device_id: int,
        firmware_version: str = "",
    ) -> tuple[int, list[Finding]]:
        """Collect + scan + enrich + persist. Returns (snapshot_id, findings)."""
        try:
            inv = await collect_inventory(ssh, firmware_version=firmware_version)
        except SlateSSHError as exc:
            logger.warning("security.scan.ssh_failed", error=str(exc))
            raise

        snapshot_id = await store.create_snapshot(device_id, inv)

        all_findings: list[Finding] = []
        errors: list[str] = []
        for src in self._sources:
            try:
                got = await src.scan(inv.packages)
            except Exception as exc:  # noqa: BLE001 - per-source isolation
                logger.warning(
                    "security.scan.source_failed",
                    source=src.id,
                    error=str(exc),
                )
                errors.append(f"{src.id}: {exc}")
                continue
            all_findings.extend(got)
            logger.info(
                "security.scan.source_ok",
                source=src.id,
                count=len(got),
            )

        merged = _merge_cross_source(all_findings)

        # Best-effort enrichment with CVE2CAPEC. Failures are non-fatal — the
        # finding rows just won't have attack_path. Surfaced as "enrich:..."
        # in the scan_error so the user sees it but isn't blocked.
        if self._enricher is not None and merged:
            unique_cves = {f.cve_id for f in merged if f.cve_id}
            try:
                paths = await self._enricher.enrich(unique_cves)
            except Exception as exc:  # noqa: BLE001
                logger.warning("security.scan.enrich_failed", error=str(exc))
                errors.append(f"enrich:cve2capec: {exc}")
                paths = {}
            for i, f in enumerate(merged):
                raw = paths.get(f.cve_id)
                if raw:
                    merged[i] = f.model_copy(update={"attack_path": AttackPath(**raw)})

        # Warm the exploit cache for every CVE we found. This is best-effort —
        # findings are surfaced even if exploit lookups fail. The /findings
        # route joins the cache at view time so daily KEV/EPSS refreshes show
        # up without re-scanning.
        if self._exploit_enricher is not None and merged:
            cve_with_cvss: dict[str, float | None] = {}
            for f in merged:
                if f.cve_id and f.cve_id.startswith("CVE-"):
                    cve_with_cvss[f.cve_id] = f.cvss_score
            if cve_with_cvss:
                try:
                    await self._exploit_enricher.enrich_for_findings(cve_with_cvss)
                    logger.info(
                        "security.scan.exploit_enrich_ok",
                        cves=len(cve_with_cvss),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "security.scan.exploit_enrich_failed", error=str(exc)
                    )
                    errors.append(f"exploit_enrich: {exc}")

        await store.replace_findings(snapshot_id, merged)

        if not self._sources:
            status = "scanned"
        elif errors and len(errors) == len(self._sources):
            status = "error"
        elif errors:
            status = "partial"
        else:
            status = "scanned"
        await store.mark_scan_complete(
            snapshot_id, status=status, error=" | ".join(errors)
        )

        logger.info(
            "security.scan.done",
            snapshot_id=snapshot_id,
            status=status,
            findings=len(merged),
        )
        return snapshot_id, merged
