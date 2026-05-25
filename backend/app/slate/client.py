"""Async wrapper around the synchronous `pyglinet` library.

`pyglinet.GlInet` is sync and maintains a background keep-alive thread that
auto-reconnects on SID expiration. We expose an async-friendly facade by
running the lib's calls in a worker thread (`asyncio.to_thread`) and
serializing access through a lock so the underlying client (which is not
thread-safe by itself) stays consistent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog

from app.exceptions import SlateRpcError, SlateUnreachableError
from app.slate.url_resolver import SlateUrlResolver

try:  # pyglinet may not be installed in some dev contexts (e.g. lint-only CI)
    from pyglinet import GlInet  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    GlInet = None  # type: ignore[assignment, misc]

logger = structlog.get_logger(__name__)


def _normalize_url(url: str) -> str:
    """Ensure the URL ends with `/rpc` (pyglinet default endpoint)."""
    stripped = url.rstrip("/")
    return stripped if stripped.endswith("/rpc") else f"{stripped}/rpc"


def _host_port(url: str) -> tuple[str, int]:
    """Extract (host, port) from an http(s)://host[:port][/path] URL.

    Used by the TCP pre-probe to fast-fail before pyglinet's blocking
    login flow. Defaults: 443 for https, 80 for http.
    """
    parsed = urlparse(url)
    host = parsed.hostname or url.split("/", 1)[0]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


@dataclass
class CircuitState:
    """Public snapshot of the breaker — surfaced to the UI / diagnostics."""

    open: bool
    consecutive_failures: int
    open_until_seconds: float  # seconds remaining; 0 if not open


class SlateClient:
    """Thin async facade over `pyglinet.GlInet`.

    Lifecycle:
      - Constructed once (typically at app startup).
      - `connect()` is lazy: called on first `call()`. Safe to call concurrently.
      - `disconnect()` should be called at shutdown.

    Reconnection on session loss is delegated to pyglinet's keep-alive thread.
    On hard errors (auth/SID), we reset the underlying client and retry once.
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        url_resolver: SlateUrlResolver | None = None,
    ) -> None:
        # `_url` is the fallback when the resolver isn't provided OR when it
        # has no reachable candidate. When the resolver IS provided, _url is
        # recomputed before each connect from `resolver.active()`.
        self._url = _normalize_url(url)
        self._username = username
        self._password = password
        self._resolver = url_resolver
        self._glinet: Any = None  # GlInet | None; typed Any so pyglinet remains optional
        self._lock = asyncio.Lock()
        # Circuit breaker state. Trips after _CB_FAILURE_THRESHOLD
        # consecutive connect/login failures, stays open for
        # _CB_OPEN_DURATION seconds during which every connect() fast-fails
        # without invoking pyglinet at all. Resets on the first success.
        # Bound to event-loop monotonic time so it's safe across timezone
        # changes / system clock jumps.
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0

    @property
    def is_connected(self) -> bool:
        return self._glinet is not None

    # Hard wall-clock limits. pyglinet uses sync `requests` without per-call
    # timeouts, which means a single stalled keep-alive thread can wedge every
    # subsequent call under our lock. asyncio.wait_for short-circuits us; the
    # session.request monkey-patch in `_build_and_login` short-circuits the
    # thread itself.
    _CONNECT_TIMEOUT = 12.0
    _CALL_TIMEOUT = 10.0
    _SESSION_REQ_TIMEOUT = (5.0, 10.0)  # (connect, read)
    # Circuit breaker tuning. Open after 3 failures, stay open 30s. The
    # TCP pre-probe runs in <2s so even when the circuit is closed, a
    # truly-unreachable Slate caps each connect() at probe-timeout + a
    # bit of pyglinet overhead instead of the 12s pyglinet timeout.
    _CB_FAILURE_THRESHOLD = 3
    _CB_OPEN_DURATION = 30.0
    _CB_PROBE_TIMEOUT = 2.0

    async def _resolve_url(self) -> str:
        """Pick the URL to use right now. If a resolver is configured, use
        its active choice; otherwise fall back to the static `_url`."""
        if self._resolver is None:
            return self._url
        active = await self._resolver.active()
        return _normalize_url(active)

    async def connect(self) -> None:
        """Open the session (idempotent). If a resolver is configured and
        the active URL has changed since the last login, the stale session
        is dropped first so we re-login on the new URL.

        Connect flow :
          1. Circuit breaker check — if open, fast-fail
          2. Already connected on the right URL? → done
          3. TCP pre-probe (2s) — fast-fail if the host is unreachable
             at the socket layer (avoids pyglinet's 12s wedge on a
             stale keep-alive thread)
          4. pyglinet login under 12s timeout
          5. Record success → reset breaker. Failure → increment, maybe trip.
        """
        # Step 1 — circuit breaker fast-fail. This is checked OUTSIDE the
        # lock so a wedged connect attempt in flight doesn't queue
        # everyone else behind it.
        loop = asyncio.get_event_loop()
        now = loop.time()
        if self._circuit_open_until > now:
            remaining = self._circuit_open_until - now
            raise SlateUnreachableError(
                f"slate breaker open ({remaining:.0f}s remaining, "
                f"{self._consecutive_failures} consecutive failures)"
            )

        async with self._lock:
            # Re-check inside the lock — another coroutine may have just
            # tripped or reset the breaker while we were waiting.
            now = loop.time()
            if self._circuit_open_until > now:
                remaining = self._circuit_open_until - now
                raise SlateUnreachableError(
                    f"slate breaker open ({remaining:.0f}s remaining)"
                )

            target_url = await self._resolve_url()
            if self._glinet is not None:
                if target_url == self._url:
                    return  # already connected to the right place
                # Resolver decided to switch URL since last login. Tear
                # down the current session and reconnect on the new URL.
                logger.info("slate.client.failover", from_=self._url, to=target_url)
                stale = self._glinet
                self._glinet = None
                try:
                    await asyncio.to_thread(stale.logout)
                except Exception:  # noqa: BLE001 - best effort
                    pass
            self._url = target_url
            if GlInet is None:
                raise SlateUnreachableError("pyglinet not installed")

            # Step 3 — TCP pre-probe. Cheap (~ms when reachable, 2s timeout
            # when not) and bypasses any stale state in pyglinet's
            # internals. A dead socket pool can't fool this — it's a
            # fresh asyncio connection.
            if not await self._probe_tcp(target_url):
                self._record_failure()
                if self._resolver is not None:
                    await self._resolver.mark_failed(
                        target_url.removesuffix("/rpc"),
                    )
                raise SlateUnreachableError(
                    f"slate TCP unreachable at {target_url} "
                    f"(probe failed in {self._CB_PROBE_TIMEOUT}s)"
                )

            # Step 4 — pyglinet login.
            try:
                self._glinet = await asyncio.wait_for(
                    asyncio.to_thread(self._build_and_login),
                    timeout=self._CONNECT_TIMEOUT,
                )
            except TimeoutError as exc:
                logger.error("slate.connect.timeout", url=self._url)
                self._record_failure()
                if self._resolver is not None:
                    await self._resolver.mark_failed(target_url.removesuffix("/rpc"))
                raise SlateUnreachableError(
                    f"Slate connect timeout after {self._CONNECT_TIMEOUT}s"
                ) from exc
            except Exception as exc:
                logger.error("slate.connect.failed", url=self._url, error=str(exc))
                self._record_failure()
                if self._resolver is not None:
                    await self._resolver.mark_failed(target_url.removesuffix("/rpc"))
                raise SlateUnreachableError(f"Cannot reach Slate at {self._url}") from exc

            # Step 5 — success.
            self._record_success()
            logger.info("slate.connect.ok", url=self._url)

    async def _probe_tcp(self, target_url: str) -> bool:
        """Open a TCP socket to (host, port) with a hard 2s timeout.

        Returns True when the socket connects, False on any error or
        timeout. Doesn't speak TLS — the goal is *reachability*, not
        cert validation (which pyglinet handles afterwards with
        verify_ssl_certificate=False anyway).
        """
        host, port = _host_port(target_url)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self._CB_PROBE_TIMEOUT,
            )
        except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning(
                "slate.probe.failed", host=host, port=port, error=str(exc),
            )
            return False
        # Be polite: close cleanly. wait_closed() can itself block on
        # some networks → wrap it.
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        except (OSError, TimeoutError, asyncio.TimeoutError):
            pass
        return True

    def _record_failure(self) -> None:
        """Increment consecutive failures, trip the breaker at threshold."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._CB_FAILURE_THRESHOLD:
            loop = asyncio.get_event_loop()
            self._circuit_open_until = loop.time() + self._CB_OPEN_DURATION
            logger.warning(
                "slate.breaker.open",
                failures=self._consecutive_failures,
                duration_s=self._CB_OPEN_DURATION,
            )

    def _record_success(self) -> None:
        """Reset breaker state after a successful connect/call."""
        if self._consecutive_failures > 0 or self._circuit_open_until > 0:
            logger.info(
                "slate.breaker.reset",
                previous_failures=self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def circuit_state(self) -> CircuitState:
        """Snapshot of the breaker — used by /api/slate/connectivity."""
        loop = asyncio.get_event_loop()
        now = loop.time()
        remaining = max(0.0, self._circuit_open_until - now)
        return CircuitState(
            open=remaining > 0,
            consecutive_failures=self._consecutive_failures,
            open_until_seconds=remaining,
        )

    async def force_reset(self) -> None:
        """Manual recovery — drops the client AND closes the breaker.

        Exposed via `POST /api/slate/force-reset` so the user has a way
        out when the breaker is open but they have reason to believe the
        Slate is back (e.g. just rebooted it manually). The next call()
        will reconnect from scratch.
        """
        async with self._lock:
            self._glinet = None
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
        logger.info("slate.client.force_reset")

    def _build_and_login(self) -> Any:
        gl = GlInet(
            url=self._url,
            username=self._username,
            password=self._password,
            verify_ssl_certificate=False,
            keep_alive=True,
        )
        gl.login()
        # Inject a default timeout into the underlying requests.Session so a
        # silent TCP stall can't wedge the keep-alive thread (which has no
        # asyncio supervision). pyglinet's keep-alive lives inside that thread.
        session = getattr(gl, "_session", None)
        if session is not None:
            original = session.request
            timeout = self._SESSION_REQ_TIMEOUT

            def _request_with_default_timeout(method, url, **kwargs):
                kwargs.setdefault("timeout", timeout)
                return original(method, url, **kwargs)

            session.request = _request_with_default_timeout  # type: ignore[assignment]
        return gl

    async def disconnect(self) -> None:
        """Close the session (best-effort, never raises)."""
        async with self._lock:
            if self._glinet is None:
                return
            client = self._glinet
            self._glinet = None
        try:
            await asyncio.to_thread(client.logout)
        except Exception as exc:  # noqa: BLE001 - best effort cleanup
            logger.warning("slate.disconnect.failed", error=str(exc))

    async def call(
        self,
        group: str,
        method: str,
        params: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        """Invoke a JSON-RPC method on the Slate.

        Args:
            group: API group name (e.g. "system", "adguardhome").
            method: Method name within the group (e.g. "get_status").
            params: Optional parameters dict/list.

        Raises:
            SlateUnreachableError: connection cannot be established.
            SlateRpcError: the RPC call returned an error payload.
        """
        await self.connect()
        return await self._call_with_retry(group, method, params)

    async def _call_with_retry(
        self,
        group: str,
        method: str,
        params: dict[str, Any] | list[Any] | None,
    ) -> Any:
        rpc_args: list[Any] = [group, method]
        if params is not None:
            rpc_args.append(params)

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._glinet.request, "call", rpc_args),
                timeout=self._CALL_TIMEOUT,
            )
            # Successful RPC — reset the breaker. We're definitely healthy
            # if the Slate just answered us a JSON-RPC reply.
            self._record_success()
            return result
        except TimeoutError as exc:
            # The worker thread is wedged — drop the client so the next call
            # rebuilds a fresh session instead of also wedging under our lock.
            # Also count it toward the breaker: a wedged call is symptomatic
            # of the same root cause as a wedged connect (stale socket).
            logger.warning(
                "slate.call.timeout", group=group, method=method, t=self._CALL_TIMEOUT
            )
            self._record_failure()
            await self._force_reset()
            raise SlateRpcError(
                f"timeout after {self._CALL_TIMEOUT}s", group=group, method=method
            ) from exc
        except Exception as exc:
            error_text = str(exc).lower()
            session_lost = any(
                marker in error_text for marker in ("auth", "sid", "session", "unauthorized")
            )
            if session_lost:
                logger.info("slate.session.lost", group=group, method=method)
                await self.disconnect()
                await self.connect()
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(self._glinet.request, "call", rpc_args),
                        timeout=self._CALL_TIMEOUT,
                    )
                except Exception as retry_exc:
                    raise SlateRpcError(
                        str(retry_exc), group=group, method=method
                    ) from retry_exc
            raise SlateRpcError(str(exc), group=group, method=method) from exc

    async def _force_reset(self) -> None:
        """Drop the (possibly wedged) underlying client without awaiting it.

        We can't kill the orphan thread that holds the stuck socket — it'll
        die when the OS-level TCP timeout fires. We just stop blocking new
        callers behind it.
        """
        async with self._lock:
            self._glinet = None
