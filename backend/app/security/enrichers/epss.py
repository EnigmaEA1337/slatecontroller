"""EPSS (Exploit Prediction Scoring System) source — FIRST.org.

EPSS scores the probability that a CVE will be exploited in the next 30
days, plus its percentile rank. Refreshed daily upstream. We bulk-query
(comma-separated CVE list, batch of 100 max) and cache nothing locally;
the orchestrator's cve_exploit_cache table is the cache.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.security.models import EPSSData

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.first.org/data/v1/epss"
_BULK_MAX = 100  # generous: API accepts more but URL length matters
_HTTP_TIMEOUT = 30.0


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class EpssSource:
    """Bulk lookup of EPSS scores for our CVEs."""

    id = "epss"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, headers={"User-Agent": "slate-controller/0.1"}
        )

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def lookup_bulk(self, cve_ids: list[str]) -> dict[str, EPSSData]:
        """Return {cve_id: EPSSData} for ids that EPSS has scored.

        Non-CVE ids are skipped silently. CVEs not in EPSS (unscored) are
        omitted from the result.
        """
        out: dict[str, EPSSData] = {}
        targets = [c for c in cve_ids if c.startswith("CVE-")]
        if not targets:
            return out
        for i in range(0, len(targets), _BULK_MAX):
            chunk = targets[i : i + _BULK_MAX]
            try:
                resp = await self._client.get(
                    _BASE_URL, params={"cve": ",".join(chunk)}
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning(
                    "security.epss.bulk_failed", offset=i, error=str(exc)
                )
                continue
            try:
                payload = resp.json()
            except ValueError:
                logger.warning("security.epss.bulk_parse_failed", offset=i)
                continue
            for entry in payload.get("data") or []:
                cve_id = entry.get("cve")
                if not cve_id:
                    continue
                try:
                    out[cve_id] = EPSSData(
                        score=float(entry["epss"]),
                        percentile=float(entry["percentile"]),
                        date=_parse_iso(entry.get("date")) or datetime.now(UTC),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    logger.info(
                        "security.epss.entry_parse_failed",
                        cve=cve_id,
                        error=str(exc),
                    )
        logger.info(
            "security.epss.bulk_done", requested=len(targets), scored=len(out)
        )
        return out
