"""Metasploit Framework module metadata source.

We pull `db/modules_metadata_base.json` (~10 MB) from the rapid7 repo and
build a CVE → list[modules] index in memory. References inside each module
are flat strings: `CVE-YYYY-NNNN`, `OSVDB-...`, `BID-...`, `URL-...` — we
filter on the CVE prefix.

Module rank (numeric in MSF) is exposed as a human label in the URL slot's
`title` since the spec calls for an `ExploitSource`-shaped result.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from app.security.models import ExploitSource

logger = structlog.get_logger(__name__)

_METADATA_URL = (
    "https://raw.githubusercontent.com/rapid7/metasploit-framework/master/"
    "db/modules_metadata_base.json"
)
_HTTP_TIMEOUT = 120.0

_RANK_LABEL = {
    0: "manual",
    100: "low",
    200: "average",
    300: "normal",
    400: "good",
    500: "great",
    600: "excellent",
}


def _rank_label(rank: int | None) -> str:
    if rank is None:
        return "?"
    return _RANK_LABEL.get(rank, str(rank))


def _parse_msf_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class MetasploitSource:
    """In-memory CVE → list[ExploitSource] index from Metasploit metadata."""

    id = "metasploit"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, headers={"User-Agent": "slate-controller/0.1"}
        )
        self._index: dict[str, list[ExploitSource]] = {}
        self._loaded_at: datetime | None = None

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    @property
    def loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def count(self) -> int:
        return sum(len(v) for v in self._index.values())

    @property
    def last_refreshed_at(self) -> datetime | None:
        return self._loaded_at

    async def refresh(self) -> int:
        logger.info("security.metasploit.refresh.start", url=_METADATA_URL)
        resp = await self._client.get(_METADATA_URL)
        resp.raise_for_status()
        data = resp.json()
        new_index: dict[str, list[ExploitSource]] = {}
        for fullname, mod in data.items():
            if not isinstance(mod, dict):
                continue
            refs = mod.get("references") or []
            cves = {
                r for r in refs if isinstance(r, str) and r.startswith("CVE-")
            }
            if not cves:
                continue
            rank = mod.get("rank")
            rank_text = _rank_label(rank if isinstance(rank, int) else None)
            mod_type = mod.get("type") or "exploit"
            module_path = mod.get("fullname") or fullname
            authors = mod.get("author") or []
            author = authors[0] if authors else None
            entry = ExploitSource(
                source="metasploit",
                url=f"https://github.com/rapid7/metasploit-framework/blob/master{mod.get('path', '')}",
                title=f"{module_path} ({mod_type}, rank={rank_text})",
                author=str(author) if author else None,
                date_published=_parse_msf_date(mod.get("disclosure_date")),
                # `verified` here doubles as "is excellent/great rank?"
                verified=isinstance(rank, int) and rank >= 500,
            )
            for cve_id in cves:
                new_index.setdefault(cve_id, []).append(entry)
        self._index = new_index
        self._loaded_at = datetime.now(UTC)
        total = sum(len(v) for v in new_index.values())
        logger.info(
            "security.metasploit.refresh.done",
            cves=len(new_index),
            modules=total,
        )
        return total

    def lookup(self, cve_id: str) -> list[ExploitSource]:
        return list(self._index.get(cve_id, ()))
