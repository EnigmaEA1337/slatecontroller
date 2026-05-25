"""CISA Known Exploited Vulnerabilities (KEV) source.

KEV is a single ~1.5 MB JSON feed updated weekly-ish. We pull it once daily,
keep the per-CVE index in memory, and look up at view time. Restart loses
the cache — refresh() is called from app lifespan if the singleton is empty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.security.models import KEVEntry

logger = structlog.get_logger(__name__)

_FEED_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
)
_HTTP_TIMEOUT = 30.0


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # KEV uses YYYY-MM-DD without tz; treat as UTC midnight.
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class CisaKevSource:
    """In-memory index of the CISA KEV catalog."""

    id = "cisa_kev"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, headers={"User-Agent": "slate-controller/0.1"}
        )
        self._index: dict[str, KEVEntry] = {}
        self._loaded_at: datetime | None = None

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    @property
    def loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def count(self) -> int:
        return len(self._index)

    @property
    def last_refreshed_at(self) -> datetime | None:
        return self._loaded_at

    async def refresh(self) -> int:
        """Fetch the feed, replace the index, return entry count."""
        logger.info("security.kev.refresh.start", url=_FEED_URL)
        resp = await self._client.get(_FEED_URL)
        resp.raise_for_status()
        data = resp.json()
        new_index: dict[str, KEVEntry] = {}
        for raw in data.get("vulnerabilities") or []:
            cve_id = raw.get("cveID")
            if not cve_id:
                continue
            new_index[cve_id] = KEVEntry(
                date_added=_parse_date(raw.get("dateAdded")) or datetime.now(UTC),
                due_date=_parse_date(raw.get("dueDate")),
                vendor=raw.get("vendorProject"),
                product=raw.get("product"),
                vulnerability_name=raw.get("vulnerabilityName"),
                short_description=raw.get("shortDescription"),
                required_action=raw.get("requiredAction"),
                known_ransomware_use=raw.get("knownRansomwareCampaignUse") == "Known",
                notes=raw.get("notes"),
            )
        self._index = new_index
        self._loaded_at = datetime.now(UTC)
        logger.info("security.kev.refresh.done", count=len(new_index))
        return len(new_index)

    def lookup(self, cve_id: str) -> KEVEntry | None:
        return self._index.get(cve_id)
