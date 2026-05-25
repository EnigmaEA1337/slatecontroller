"""CERT-FR / ANSSI bulletins source.

CERT-FR (the French national CSIRT, ANSSI) publishes two RSS feeds:
  - /alerte/feed/  — high-impact bulletins, often "actively exploited" or
    high-profile (Ivanti, Fortinet, Citrix, Microsoft, Cisco, …).
    ~30-50 items in the feed (latest year-ish).
  - /avis/feed/    — security advisories, several per week. ~40-50 items.

The RSS descriptions are excerpts — they don't enumerate all CVE IDs. So
for each bulletin we fetch the HTML page once (small, ~17 KB) and regex
out every CVE-XXXX-NNNN reference. The mapping is held in memory and
refreshed daily by the security scheduler.

We deliberately keep this lightweight (no `feedparser` dep): the feeds are
plain RSS 2.0 and we already pull HTML pages anyway.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

_ALERTE_FEED = "https://www.cert.ssi.gouv.fr/alerte/feed/"
_AVIS_FEED = "https://www.cert.ssi.gouv.fr/avis/feed/"
_HTTP_TIMEOUT = 30.0
_PAGE_CONCURRENCY = 6
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}")
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_TAG_RE = lambda tag: re.compile(  # noqa: E731
    rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", re.DOTALL
)


def _parse_rfc822_date(value: str) -> datetime | None:
    """Parse RSS pubDate (e.g. 'Tue, 26 Aug 2025 00:00:00 +0000') tolerantly."""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _extract_tag(item_xml: str, tag: str) -> str:
    m = _TAG_RE(tag).search(item_xml)
    return m.group(1).strip() if m else ""


# Heuristic flags inferred from bulletin text — case-insensitive contains.
_EXPLOITED_PHRASES = (
    "activement exploit",
    "exploitation active",
    "preuve de concept",
    "preuves de concept",
    "publiquement disponible",
    "exploit publiquement",
)
_RANSOM_PHRASES = ("rançongiciel", "ransomware", "ransomgiciel")


def _has_any(text_lower: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text_lower for p in phrases)


class CertFrBulletin(BaseModel):
    """One CERT-FR bulletin (alerte or avis)."""

    ref: str                       # "CERTFR-2026-ALE-005"
    kind: Literal["alerte", "avis"]
    title: str
    url: str
    pub_date: datetime | None = None
    # Set to True if the bulletin text contains phrases like "activement exploitée"
    # or "preuve de concept disponible" — these flag higher urgency.
    actively_exploited: bool = False
    ransomware_mentioned: bool = False


_REF_RE = re.compile(r"CERTFR-\d{4}-(?:ALE|AVI)-\d+", re.IGNORECASE)


def _extract_ref(url: str) -> str:
    """Pull 'CERTFR-2026-ALE-005' from the bulletin URL."""
    m = _REF_RE.search(url)
    return m.group(0).upper() if m else url


class CertFrSource:
    """In-memory CVE → list[CertFrBulletin] index from CERT-FR feeds."""

    id = "cert_fr"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "slate-controller/0.1"},
        )
        self._index: dict[str, list[CertFrBulletin]] = {}
        self._loaded_at: datetime | None = None

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    @property
    def loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def count(self) -> int:
        """Number of distinct CVEs covered by at least one bulletin."""
        return len(self._index)

    @property
    def last_refreshed_at(self) -> datetime | None:
        return self._loaded_at

    async def refresh(self) -> int:
        logger.info("security.certfr.refresh.start")
        feeds: list[tuple[Literal["alerte", "avis"], str]] = [
            ("alerte", _ALERTE_FEED),
            ("avis", _AVIS_FEED),
        ]
        items_to_fetch: list[tuple[Literal["alerte", "avis"], str, str, datetime | None]] = []
        for kind, url in feeds:
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning(
                    "security.certfr.feed_failed", kind=kind, error=str(exc)
                )
                continue
            for item_xml in _ITEM_RE.findall(resp.text):
                title = _extract_tag(item_xml, "title")
                link = _extract_tag(item_xml, "link")
                pub = _parse_rfc822_date(_extract_tag(item_xml, "pubDate"))
                if not link:
                    continue
                items_to_fetch.append((kind, title, link, pub))

        logger.info("security.certfr.feeds_parsed", items=len(items_to_fetch))

        # Fetch each bulletin page (~17 KB) with bounded concurrency.
        sem = asyncio.Semaphore(_PAGE_CONCURRENCY)
        new_index: dict[str, list[CertFrBulletin]] = {}

        async def _fetch_page(
            kind: Literal["alerte", "avis"],
            title: str,
            link: str,
            pub: datetime | None,
        ) -> None:
            async with sem:
                try:
                    r = await self._client.get(link)
                    r.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.info(
                        "security.certfr.page_failed",
                        url=link,
                        error=str(exc),
                    )
                    return
            text = r.text
            cves = sorted(set(_CVE_RE.findall(text)))
            if not cves:
                return
            text_lower = text.lower()
            bulletin = CertFrBulletin(
                ref=_extract_ref(link),
                kind=kind,
                title=title or _extract_ref(link),
                url=link,
                pub_date=pub,
                actively_exploited=_has_any(text_lower, _EXPLOITED_PHRASES),
                ransomware_mentioned=_has_any(text_lower, _RANSOM_PHRASES),
            )
            for cve_id in cves:
                new_index.setdefault(cve_id, []).append(bulletin)

        await asyncio.gather(
            *(_fetch_page(k, t, l, p) for k, t, l, p in items_to_fetch)
        )

        self._index = new_index
        self._loaded_at = datetime.now(UTC)
        logger.info(
            "security.certfr.refresh.done",
            cves=len(new_index),
            bulletins=sum(len(v) for v in new_index.values()),
        )
        return len(new_index)

    def lookup(self, cve_id: str) -> list[CertFrBulletin]:
        return list(self._index.get(cve_id, ()))
