"""OSV.dev source — primary CVE matcher for V1.

OSV exposes a batch query endpoint that does server-side version matching
across ecosystems (Alpine, Debian, GitHub-reviewed, etc.). For each
(package_name, version) we send, it returns the list of vulnerability IDs
that affect that version. We then fetch full details for each unique ID
(per-process in-memory cache) and synthesize a `Finding`.

Why this is good enough for OpenWrt without an OpenWrt ecosystem:
  - Most OpenWrt userspace is upstream OSS (busybox, openssl, dnsmasq,
    dropbear, curl, libpcap, ...). Those have entries in
    Alpine/Debian/GHSA databases that OSV aggregates.
  - Result is necessarily *noisy* (cross-ecosystem fix may not have landed
    in the OpenWrt build) — the UI flags this with a clear caveat.

Things this source intentionally does NOT do:
  - Match GL.iNet vendor packages (filtered out by `vendor_specific=True`).
  - Deduplicate across other sources (the orchestrator's job).
  - CVSS enrichment beyond what's already in the OSV record — NVD source
    (Phase B) fills in missing scores.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any

import httpx
import structlog

from app.security.models import Finding, Package, Severity
from app.security.sources.base import VulnSource

logger = structlog.get_logger(__name__)

_BATCH_URL = "https://api.osv.dev/v1/querybatch"
_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
# OSV's hard limit is 1000 but huge batches mean huge response payloads
# (busybox alone returns 200+ vuln IDs) and easily exceed our HTTP timeout.
# 50 keeps each request snappy and gives us natural retry granularity.
_BATCH_MAX = 50
_BATCH_RETRIES = 2
_DETAILS_CONCURRENCY = 6
_HTTP_TIMEOUT = 60.0

_CVSS_BASE_RE = re.compile(r"^CVSS:3\.[01]/")


def _cvss_to_severity(score: float) -> Severity:
    """Map CVSS v3 base score to our severity buckets (NVD convention)."""
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return "unknown"


def _parse_cvss_vector(vector: str) -> float | None:
    """Crude CVSS v3 base-score parser.

    We avoid pulling a cvss lib for one job. Returns None when the vector is
    malformed; NVD enrichment (Phase B) will fill in proper scores then.
    """
    if not _CVSS_BASE_RE.match(vector):
        return None
    parts = dict(p.split(":", 1) for p in vector.split("/")[1:] if ":" in p)
    av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}.get(parts.get("AV", ""))
    ac = {"L": 0.77, "H": 0.44}.get(parts.get("AC", ""))
    ui = {"N": 0.85, "R": 0.62}.get(parts.get("UI", ""))
    scope = parts.get("S", "")
    c = {"N": 0.0, "L": 0.22, "H": 0.56}.get(parts.get("C", ""))
    i = {"N": 0.0, "L": 0.22, "H": 0.56}.get(parts.get("I", ""))
    a = {"N": 0.0, "L": 0.22, "H": 0.56}.get(parts.get("A", ""))
    if None in (av, ac, ui, c, i, a):
        return None
    pr_unchanged = {"N": 0.85, "L": 0.62, "H": 0.27}
    pr_changed = {"N": 0.85, "L": 0.68, "H": 0.50}
    pr = (pr_changed if scope == "C" else pr_unchanged).get(parts.get("PR", ""))
    if pr is None:
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 6.42 * iss if scope == "U" else 7.52 * (iss - 0.029) - 3.25 * pow(iss - 0.02, 15)
    exploit = 8.22 * av * ac * pr * ui
    if impact <= 0:
        return 0.0
    base = min(impact + exploit, 10) if scope == "U" else min(1.08 * (impact + exploit), 10)
    return round(base + 0.04999, 1)


def _extract_severity(
    vuln: dict[str, Any],
) -> tuple[Severity, float | None, str | None]:
    """Pull (severity_bucket, cvss_score, cvss_vector) from an OSV record."""
    for s in vuln.get("severity", []) or []:
        if s.get("type") in ("CVSS_V3", "CVSS_V31"):
            vector = str(s.get("score", ""))
            score = _parse_cvss_vector(vector)
            if score is not None:
                return _cvss_to_severity(score), score, vector
    for aff in vuln.get("affected", []) or []:
        ds = aff.get("database_specific") or {}
        sev = (ds.get("severity") or "").lower()
        if sev in ("critical", "high", "medium", "low"):
            return sev, None, None  # type: ignore[return-value]
    return "unknown", None, None


def _pick_cve_id(vuln: dict[str, Any]) -> str:
    """Prefer the CVE alias; fall back to the OSV id.

    OSV puts upstream CVE ids in either `aliases` or `upstream` depending on
    the source database (Alpine uses `upstream`, GHSA uses `aliases`).
    """
    vid = vuln.get("id", "")
    if vid.startswith("CVE-"):
        return vid
    for src_field in ("aliases", "upstream"):
        for alias in vuln.get(src_field, []) or []:
            if isinstance(alias, str) and alias.startswith("CVE-"):
                return alias
    return vid


def _pick_fixed_in(vuln: dict[str, Any], package_name: str) -> str | None:
    """Best-effort fix version from any affected range matching the package.

    Returns None for "0" / "0.0" placeholders which some ecosystems use to
    mean "no fix released yet" — we'd rather show empty than a fake version.
    """
    target = package_name.lower()
    for aff in vuln.get("affected", []) or []:
        pkg = aff.get("package") or {}
        if (pkg.get("name") or "").lower() != target:
            continue
        for rng in aff.get("ranges") or []:
            for ev in rng.get("events") or []:
                fixed = str(ev.get("fixed") or "").strip()
                if fixed and fixed not in {"0", "0.0", "0.0.0"}:
                    return fixed
    return None


_SEVERITY_RANK: dict[Severity, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}


def _merge_finding(existing: Finding, new: Finding) -> Finding:
    """Keep the most informative of two findings sharing (cve_id, package_name)."""
    keep_new = (
        _SEVERITY_RANK[new.severity] > _SEVERITY_RANK[existing.severity]
        or (existing.cvss_score is None and new.cvss_score is not None)
        or (not existing.fixed_in and bool(new.fixed_in))
    )
    base = new if keep_new else existing
    other = existing if keep_new else new
    # Prefer whichever record actually carries a vector string.
    vector = base.cvss_vector or other.cvss_vector
    aliases = list({*base.aliases, *other.aliases})
    return base.model_copy(update={"aliases": aliases, "cvss_vector": vector})


def _pick_url(vuln: dict[str, Any]) -> str | None:
    refs = vuln.get("references") or []
    for r in refs:
        if r.get("type") in ("ADVISORY", "WEB"):
            return r.get("url")
    if refs:
        return refs[0].get("url")
    vid = vuln.get("id")
    return f"https://osv.dev/vulnerability/{vid}" if vid else None


def _summary(vuln: dict[str, Any]) -> str:
    text = vuln.get("summary") or vuln.get("details") or ""
    return text[:2000]


class OsvSource(VulnSource):
    """Query OSV.dev for vulnerabilities in installed packages."""

    id = "osv"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            http2=False,
            headers={"User-Agent": "slate-controller/0.1"},
        )
        # Per-process cache: vuln_id → full OSV record. Survives across scans
        # within a uvicorn lifetime.
        self._vuln_cache: dict[str, dict[str, Any]] = {}

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def scan(self, packages: Sequence[Package]) -> list[Finding]:
        # OSV has no useful coverage for vendor-specific packages — skip them
        # so we don't generate confusing UNKNOWN findings.
        unique: dict[tuple[str, str], Package] = {}
        for p in packages:
            if p.vendor_specific:
                continue
            unique.setdefault((p.name, p.upstream_version), p)

        if not unique:
            return []

        ordered = list(unique.values())
        # batch_results[i] = list of vuln dicts {"id": ..., "modified": ...}
        batch_results = await self._batch_query(ordered)

        # Unique vuln IDs we need to enrich.
        ids: set[str] = set()
        for hits in batch_results:
            for v in hits:
                vid = v.get("id")
                if vid:
                    ids.add(vid)

        await self._prefetch_details(ids)

        # Dedup by (cve_id, package_name) — OSV aggregates the same upstream
        # CVE from Alpine + Debian + GHSA + RHEL + … each as a separate record.
        # Keep the most informative copy (highest severity, has CVSS, has fix).
        deduped: dict[tuple[str, str], Finding] = {}
        for pkg, hits in zip(ordered, batch_results, strict=False):
            for v in hits:
                vid = v.get("id")
                detail = self._vuln_cache.get(vid) if vid else None
                if not detail:
                    finding = Finding(
                        cve_id=vid or "UNKNOWN",
                        package_name=pkg.name,
                        package_version=pkg.version,
                        source="osv",
                        url=f"https://osv.dev/vulnerability/{vid}" if vid else None,
                    )
                else:
                    severity, cvss, vector = _extract_severity(detail)
                    aliases = [
                        a
                        for src_field in ("aliases", "upstream")
                        for a in (detail.get(src_field) or [])
                        if isinstance(a, str)
                    ]
                    finding = Finding(
                        cve_id=_pick_cve_id(detail),
                        package_name=pkg.name,
                        package_version=pkg.version,
                        severity=severity,
                        source="osv",
                        fixed_in=_pick_fixed_in(detail, pkg.name),
                        url=_pick_url(detail),
                        summary=_summary(detail),
                        cvss_score=cvss,
                        cvss_vector=vector,
                        aliases=aliases,
                    )
                key = (finding.cve_id, finding.package_name)
                prev = deduped.get(key)
                deduped[key] = _merge_finding(prev, finding) if prev else finding
        return list(deduped.values())

    async def _batch_query(self, ordered: list[Package]) -> list[list[dict[str, Any]]]:
        """Send the batch query in <=_BATCH_MAX chunks; returns per-package hit list.

        Each chunk has its own retry loop with exponential backoff. A single
        chunk failure raises, but other chunks aren't impacted — the partial
        results we return reflect what we actually got.
        """
        results: list[list[dict[str, Any]]] = []
        for i in range(0, len(ordered), _BATCH_MAX):
            chunk = ordered[i : i + _BATCH_MAX]
            body = {
                "queries": [
                    {"package": {"name": p.name}, "version": p.upstream_version}
                    for p in chunk
                ]
            }
            last_exc: Exception | None = None
            for attempt in range(_BATCH_RETRIES + 1):
                try:
                    resp = await self._client.post(_BATCH_URL, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    for r in data.get("results") or []:
                        results.append(r.get("vulns") or [])
                    last_exc = None
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    last_exc = exc
                    logger.info(
                        "security.osv.batch_retry",
                        attempt=attempt + 1,
                        max_attempts=_BATCH_RETRIES + 1,
                        chunk_offset=i,
                        error=str(exc),
                    )
                    # Cap exponential backoff at 30s — without this, attempt=10
                    # would sleep 1024s, stalling a profile apply for ~17min on
                    # a flaky OSV upstream. 30s is enough to absorb most rate
                    # limits without making the user think the app is hung.
                    await asyncio.sleep(min(2 ** attempt, 30))
            if last_exc is not None:
                logger.warning(
                    "security.osv.batch_failed", chunk_offset=i, error=str(last_exc)
                )
                raise RuntimeError(f"OSV batch query failed: {last_exc}") from last_exc
        return results

    async def _prefetch_details(self, ids: set[str]) -> None:
        """Fetch full vuln records for any IDs not in cache. Failures are logged
        and skipped — a missing detail just yields a thin Finding."""
        missing = [vid for vid in ids if vid not in self._vuln_cache]
        if not missing:
            return
        sem = asyncio.Semaphore(_DETAILS_CONCURRENCY)

        async def fetch(vid: str) -> None:
            async with sem:
                try:
                    r = await self._client.get(_VULN_URL.format(id=vid))
                    r.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.info("security.osv.detail_failed", id=vid, error=str(exc))
                    return
            try:
                self._vuln_cache[vid] = r.json()
            except ValueError as exc:
                logger.info("security.osv.detail_parse_failed", id=vid, error=str(exc))

        await asyncio.gather(*(fetch(v) for v in missing))
