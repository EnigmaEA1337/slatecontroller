"""GitHub PoC source via nomi-sec/PoC-in-GitHub.

This repo maintains per-CVE JSON files listing GitHub repositories that
publish a proof-of-concept for that CVE. Path layout:
    raw.githubusercontent.com/nomi-sec/PoC-in-GitHub/master/{YEAR}/{CVE-id}.json

Per-CVE fetches are cheap (~5-50 KB). We don't keep a global index —
results are cached per CVE in cve_exploit_cache by the orchestrator.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import structlog

from app.security.models import ExploitSource

logger = structlog.get_logger(__name__)

_BASE_URL = "https://raw.githubusercontent.com/nomi-sec/PoC-in-GitHub/master"
_CVE_YEAR_RE = re.compile(r"^CVE-(\d{4})-")
_HTTP_TIMEOUT = 15.0


def _parse_github_date(value: str | None) -> datetime | None:
    if not value:
        return None
    # GitHub uses "2024-01-02T03:04:05Z"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class GitHubPocSource:
    """Per-CVE lookup of GitHub PoC repos."""

    id = "github_poc"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, headers={"User-Agent": "slate-controller/0.1"}
        )

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def lookup(self, cve_id: str) -> list[ExploitSource]:
        """Return list of PoC repos for this CVE. Empty on 404 or non-CVE."""
        m = _CVE_YEAR_RE.match(cve_id)
        if not m:
            return []
        year = m.group(1)
        url = f"{_BASE_URL}/{year}/{cve_id}.json"
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            logger.info("security.github_poc.fetch_failed", cve=cve_id, error=str(exc))
            return []
        if resp.status_code == 404:
            return []
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.info(
                "security.github_poc.bad_status",
                cve=cve_id,
                status=resp.status_code,
                error=str(exc),
            )
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        if not isinstance(data, list):
            return []
        out: list[ExploitSource] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append(
                ExploitSource(
                    source="github",
                    url=str(item.get("html_url") or ""),
                    title=item.get("description") or item.get("name"),
                    author=(item.get("owner") or {}).get("login"),
                    date_published=_parse_github_date(item.get("updated_at")),
                    stars=item.get("stargazers_count"),
                )
            )
        return out
