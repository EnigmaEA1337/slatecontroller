"""Enrich findings with CVE → CWE → CAPEC → ATT&CK technique chain.

Source: https://github.com/Galeax/CVE2CAPEC (GPL-3.0). We never ship their
data — only fetch over HTTPS at scan time, like we do with OSV. That keeps
the controller's MIT license clean.

Strategy:
  1. Per-CVE cache in `cve_attack_path_cache` table (TTL 24 h).
  2. For CVEs missing from cache, group by year, fetch
     `database/CVE-YYYY.jsonl` once per year, hydrate cache for *all* CVE
     in that year file (cheap since the file is in memory anyway).
  3. Hand back a dict keyed by CVE id, with shape:
       {"cwe": [...], "capec": [...], "techniques": [...], "atlas": []}

Year file ≈ 35 MB for recent years. We parse line-by-line to avoid loading
the whole text twice in memory. Only one year is in memory at a time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import CveAttackPathCacheRow

logger = structlog.get_logger(__name__)

_RAW_URL = (
    "https://raw.githubusercontent.com/Galeax/CVE2CAPEC/main/database/CVE-{year}.jsonl"
)
_CACHE_TTL = timedelta(hours=24)
_CVE_YEAR_RE = re.compile(r"^CVE-(\d{4})-")
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _year_of(cve_id: str) -> str | None:
    m = _CVE_YEAR_RE.match(cve_id)
    return m.group(1) if m else None


class Cve2CapecEnricher:
    """Look up the attack-path chain for a batch of CVE ids."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._sf = session_factory
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "slate-controller/0.1"},
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def enrich(
        self, cve_ids: Iterable[str]
    ) -> dict[str, dict[str, list[str]] | None]:
        """Return {cve_id: attack_path | None} for every input id.

        Non-CVE ids (GHSA-…, ALPINE-…) silently get None — the dataset only
        covers `CVE-YYYY-NNNN` form.
        """
        ids = sorted({c for c in cve_ids if c})
        if not ids:
            return {}

        cached, stale = await self._read_cache(ids)
        out: dict[str, dict[str, list[str]] | None] = dict(cached)

        # Group stale + missing by year.
        missing = [c for c in ids if c not in out]
        years_needed: set[str] = set()
        for c in missing + list(stale):
            y = _year_of(c)
            if y:
                years_needed.add(y)
        for y in sorted(years_needed):
            try:
                year_data = await self._fetch_year(y)
            except httpx.HTTPError as exc:
                logger.warning("security.cve2capec.fetch_failed", year=y, error=str(exc))
                continue
            await self._upsert_cache(year_data)
            for cve_id, path in year_data.items():
                out[cve_id] = path

        # Any input id still not found → None (no entry in dataset).
        for c in ids:
            out.setdefault(c, None)
        return out

    # ---------------------------- internals ---------------------------- #

    async def _read_cache(
        self, cve_ids: list[str]
    ) -> tuple[dict[str, dict[str, list[str]]], set[str]]:
        """Return (fresh_hits, stale_ids). Stale means present but expired."""
        cutoff = datetime.now(UTC) - _CACHE_TTL
        fresh: dict[str, dict[str, list[str]]] = {}
        stale: set[str] = set()
        async with self._sf() as s:
            stmt = select(CveAttackPathCacheRow).where(
                CveAttackPathCacheRow.cve_id.in_(cve_ids)
            )
            rows = list((await s.scalars(stmt)).all())
        for r in rows:
            # SQLite drops tzinfo on round-trip even with DateTime(timezone=True).
            # Assume UTC for the stored value so the comparison works.
            fetched_at = r.fetched_at
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            if fetched_at >= cutoff:
                fresh[r.cve_id] = r.attack_path_json
            else:
                stale.add(r.cve_id)
        return fresh, stale

    async def _fetch_year(self, year: str) -> dict[str, dict[str, list[str]]]:
        """Stream-parse one year file → {cve_id: {cwe, capec, techniques, atlas}}."""
        url = _RAW_URL.format(year=year)
        logger.info("security.cve2capec.fetch_start", year=year, url=url)
        resp = await self._client.get(url)
        resp.raise_for_status()
        text = resp.text
        out: dict[str, dict[str, list[str]]] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # File layout: {"CVE-YYYY-NNNN": {"CWE": [...], "CAPEC": [...], ...}}
            for cve_id, payload in obj.items():
                if not isinstance(payload, dict):
                    continue
                cwe = [str(x) for x in (payload.get("CWE") or [])]
                capec = [str(x) for x in (payload.get("CAPEC") or [])]
                # The dataset stores ATT&CK techniques as bare ids like "1027"
                # or sub-techniques like "1574.006". Prefix with "T" for the UI
                # so URLs to attack.mitre.org work directly.
                techniques = [
                    f"T{x}" if not str(x).startswith("T") else str(x)
                    for x in (payload.get("TECHNIQUES") or [])
                ]
                atlas = [str(x) for x in (payload.get("ATLAS") or [])]
                # Skip empty entries — they pollute the cache and confuse UX.
                if not (cwe or capec or techniques or atlas):
                    continue
                out[cve_id] = {
                    "cwe": cwe,
                    "capec": capec,
                    "techniques": techniques,
                    "atlas": atlas,
                }
        logger.info(
            "security.cve2capec.fetch_done", year=year, cves=len(out)
        )
        return out

    async def _upsert_cache(
        self, year_data: dict[str, dict[str, list[str]]]
    ) -> None:
        if not year_data:
            return
        now = datetime.now(UTC)
        rows = [
            {"cve_id": cve_id, "attack_path_json": path, "fetched_at": now}
            for cve_id, path in year_data.items()
        ]
        async with self._sf() as s:
            # SQLite upsert: ON CONFLICT(cve_id) DO UPDATE.
            stmt = sqlite_insert(CveAttackPathCacheRow).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["cve_id"],
                set_={
                    "attack_path_json": stmt.excluded.attack_path_json,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            await s.execute(stmt)
            await s.commit()
