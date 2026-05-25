"""Async wrapper around the synchronous `pyglinet` library.

`pyglinet.GlInet` is sync and maintains a background keep-alive thread that
auto-reconnects on SID expiration. We expose an async-friendly facade by
running the lib's calls in a worker thread (`asyncio.to_thread`) and
serializing access through a lock so the underlying client (which is not
thread-safe by itself) stays consistent.
"""

from __future__ import annotations

import asyncio
from typing import Any

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
        is dropped first so we re-login on the new URL."""
        async with self._lock:
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
            try:
                self._glinet = await asyncio.wait_for(
                    asyncio.to_thread(self._build_and_login),
                    timeout=self._CONNECT_TIMEOUT,
                )
            except TimeoutError as exc:
                logger.error("slate.connect.timeout", url=self._url)
                if self._resolver is not None:
                    await self._resolver.mark_failed(target_url.removesuffix("/rpc"))
                raise SlateUnreachableError(
                    f"Slate connect timeout after {self._CONNECT_TIMEOUT}s"
                ) from exc
            except Exception as exc:
                logger.error("slate.connect.failed", url=self._url, error=str(exc))
                if self._resolver is not None:
                    await self._resolver.mark_failed(target_url.removesuffix("/rpc"))
                raise SlateUnreachableError(f"Cannot reach Slate at {self._url}") from exc
            logger.info("slate.connect.ok", url=self._url)

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
            return await asyncio.wait_for(
                asyncio.to_thread(self._glinet.request, "call", rpc_args),
                timeout=self._CALL_TIMEOUT,
            )
        except TimeoutError as exc:
            # The worker thread is wedged — drop the client so the next call
            # rebuilds a fresh session instead of also wedging under our lock.
            logger.warning(
                "slate.call.timeout", group=group, method=method, t=self._CALL_TIMEOUT
            )
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
