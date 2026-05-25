"""URL resolver with LAN ↔ Tailscale automatic failover.

The Slate is reachable through multiple paths:
  - LAN (192.168.8.1 typically) — fast, low-latency, no NAT traversal.
  - Tailscale (100.x.x.x) — works from anywhere with WAN access; survives
    being moved off the local network.

Hardcoding a single URL in SLATE_URL is fragile: a firewall reload on the
Slate temporarily disrupts iptables (1-2s), which kills active LAN
connections and can dirty the controller's DHCP lease. If the admin is on
4G/mobile, the LAN URL becomes unreachable entirely.

This resolver tries each candidate URL in order and caches the first that
answers a quick TCP probe (port 22 by default = SSH). Cache TTL is short
(10s) so a path coming back online is picked up quickly. Callers can also
call `mark_failed(url)` after a network error to force an immediate
re-probe on the next access.

Used by `SlateSSH` and `SlateClient` to transparently re-target the active
URL on each connection bootstrap. The admin doesn't need to touch
SLATE_URL when the network path changes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


def _extract_host(url: str) -> str:
    """Extract host from `https://host[:port]` or bare `host`. Same idea as
    SlateSSH's local helper, duplicated to avoid an import cycle."""
    raw = url.strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.rstrip("/")
    if ":" in raw and not raw.startswith("["):
        # strip port part (host:port)
        raw = raw.split(":", 1)[0]
    return raw


@dataclass
class UrlProbeResult:
    url: str
    host: str
    reachable: bool
    latency_ms: float | None
    last_probed_at: float  # monotonic seconds


class SlateUrlResolver:
    """Decides which Slate URL to use at any given moment.

    The first URL in `urls` is preferred (typically LAN for low latency).
    On miss, the next is tried. The active choice is cached for `cache_ttl`
    seconds to avoid probing on every SSH call.
    """

    def __init__(
        self,
        urls: list[str],
        *,
        probe_port: int = 22,
        probe_timeout: float = 2.0,
        cache_ttl: float = 10.0,
    ) -> None:
        if not urls:
            raise ValueError("SlateUrlResolver requires at least one URL")
        # Deduplicate while preserving order (LAN/Tailscale can be the same
        # if the admin sets them equal — silently collapse).
        seen: set[str] = set()
        self._urls: list[str] = []
        for u in urls:
            normalized = u.strip().rstrip("/")
            if normalized and normalized not in seen:
                self._urls.append(normalized)
                seen.add(normalized)
        self._active_url: str = self._urls[0]
        self._last_probe_at: float = 0.0
        self._probe_port = probe_port
        self._probe_timeout = probe_timeout
        self._cache_ttl = cache_ttl
        self._lock = asyncio.Lock()
        # Last probe results for the /connectivity endpoint.
        self._last_results: list[UrlProbeResult] = []

    @property
    def candidates(self) -> list[str]:
        return list(self._urls)

    async def set_urls(self, urls: list[str]) -> None:
        """Hot-swap the candidate list. Called by the PATCH device endpoint
        after the user edits `admin_urls`. Forces a re-probe so the next
        `active()` call doesn't return a stale URL that's no longer in the
        list."""
        if not urls:
            raise ValueError("set_urls requires at least one URL")
        async with self._lock:
            seen: set[str] = set()
            new_list: list[str] = []
            for u in urls:
                normalized = u.strip().rstrip("/")
                if normalized and normalized not in seen:
                    new_list.append(normalized)
                    seen.add(normalized)
            self._urls = new_list
            # If the current active URL is no longer a candidate, drop it
            # to the first of the new list — _probe_all will fix it.
            if self._active_url not in new_list:
                self._active_url = new_list[0]
            self._last_probe_at = 0.0  # force fresh probe
        logger.info("slate_url.urls_updated", count=len(new_list))

    @property
    def active_url(self) -> str:
        """Synchronous fast-path: return whatever URL was last decided to
        be active. Doesn't trigger a probe — use `active()` for that."""
        return self._active_url

    async def active(self) -> str:
        """Return the URL to use right now. Probes if cache expired."""
        now = time.monotonic()
        if now - self._last_probe_at < self._cache_ttl:
            return self._active_url
        async with self._lock:
            # Double-check after acquiring the lock — another coroutine
            # may have refreshed during the wait.
            if now - self._last_probe_at < self._cache_ttl:
                return self._active_url
            await self._probe_all()
            return self._active_url

    async def mark_failed(self, url: str) -> None:
        """Caller signals that a connection to `url` just errored.

        Force the next `active()` call to re-probe (don't trust the cache).
        Doesn't itself switch the active URL — that happens at the next
        probe so we get fresh info about every candidate, not just blind
        failover.
        """
        self._last_probe_at = 0.0
        logger.info("slate_url.marked_failed", url=url)

    async def force_refresh(self) -> list[UrlProbeResult]:
        """Run a probe right now and return the results. Used by the API
        `/connectivity` endpoint and after the admin clicks a refresh
        button in the UI."""
        async with self._lock:
            await self._probe_all()
        return list(self._last_results)

    @property
    def last_results(self) -> list[UrlProbeResult]:
        """Snapshot of the last probe results without triggering a new one."""
        return list(self._last_results)

    async def _probe_all(self) -> None:
        """Probe every candidate in parallel, update active URL to first
        reachable. Updates `_last_results` and `_last_probe_at`."""
        coros = [self._probe(url) for url in self._urls]
        results = await asyncio.gather(*coros)
        self._last_results = results
        self._last_probe_at = time.monotonic()
        for r in results:
            if r.reachable:
                if r.url != self._active_url:
                    logger.info(
                        "slate_url.failover",
                        from_=self._active_url, to=r.url,
                        reason="first reachable in priority order",
                    )
                self._active_url = r.url
                return
        # No URL reachable: keep _active_url unchanged so callers get a
        # consistent error message ("can't reach <preferred>") rather than
        # a moving target.
        logger.warning(
            "slate_url.all_unreachable",
            candidates=[r.url for r in results],
        )

    async def _probe(self, url: str) -> UrlProbeResult:
        """Quick TCP connect to (host, probe_port). Cheap, no auth, no
        protocol speak — just "is the port reachable in time"."""
        host = _extract_host(url)
        start = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, self._probe_port),
                timeout=self._probe_timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            latency_ms = (time.monotonic() - start) * 1000
            return UrlProbeResult(
                url=url, host=host, reachable=True,
                latency_ms=round(latency_ms, 1),
                last_probed_at=time.monotonic(),
            )
        except (OSError, TimeoutError, asyncio.TimeoutError):
            return UrlProbeResult(
                url=url, host=host, reachable=False,
                latency_ms=None,
                last_probed_at=time.monotonic(),
            )
