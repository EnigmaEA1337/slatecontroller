"""SSH layer to the Slate.

Used when the JSON-RPC API doesn't expose what we need — UCI reads for
hardening checks (dropbear PasswordAuth, upnpd config), and eventually
Phase 2b UCI writes for profile activation.

We hold a single persistent SSH connection per `SlateSSH` instance, gated
by an asyncio lock so concurrent callers serialize. The connection is
re-opened lazily if dropped (router reboot, idle timeout, etc.).

Security note: host key verification is disabled (`known_hosts=None`).
That's acceptable for a self-hosted admin tool talking to a router on the
trusted LAN, where there's no realistic MITM threat. If you ever expose
this backend across an untrusted network, plumb in proper known_hosts.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlparse

import asyncssh
import structlog

from app.exceptions import SlateError
from app.slate.url_resolver import SlateUrlResolver

logger = structlog.get_logger(__name__)


class SlateSSHError(SlateError):
    """Any SSH-layer failure (connect, exec, channel)."""


@dataclass(frozen=True)
class SSHResult:
    stdout: str
    stderr: str
    exit_status: int

    @property
    def ok(self) -> bool:
        return self.exit_status == 0


def _extract_host(slate_url: str) -> str:
    """Pull the bare hostname/IP out of e.g. `https://192.168.8.1/rpc`."""
    if "://" not in slate_url:
        return slate_url.split("/", 1)[0]
    parsed = urlparse(slate_url)
    return parsed.hostname or slate_url


class SlateSSH:
    """One persistent SSH connection to the Slate, serialized by an asyncio lock.

    Two modes:
      - Static host (`slate_url`) — legacy. The host is set once and never
        changes for the lifetime of the instance.
      - URL resolver (`url_resolver`) — preferred. The host is re-queried
        from the resolver on every (re)connect, enabling transparent
        LAN ↔ Tailscale ↔ <custom> failover. The static `slate_url` is
        ignored if a resolver is provided.
    """

    def __init__(
        self,
        slate_url: str,
        username: str,
        password: str,
        *,
        port: int = 22,
        timeout: float = 10.0,
        private_key_pem: str | None = None,
        url_resolver: SlateUrlResolver | None = None,
    ) -> None:
        self._resolver = url_resolver
        if url_resolver is not None:
            # Initial host = the resolver's last known active. Will be
            # re-checked on first connect attempt.
            self._host = _extract_host(url_resolver.active_url)
        else:
            self._host = _extract_host(slate_url)
        self._username = username
        self._password = password
        self._port = port
        self._timeout = timeout
        self._private_key_pem = private_key_pem
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return self._host

    @property
    def auth_mode(self) -> str:
        """'key' if a private key is loaded, else 'password'."""
        return "key" if self._private_key_pem else "password"

    async def use_private_key(self, private_key_pem: str | None) -> None:
        """Swap auth mode (key ↔ password). Drops the current connection."""
        async with self._lock:
            self._private_key_pem = private_key_pem
            await self._drop_locked()

    async def _ensure_connected(self) -> asyncssh.SSHClientConnection:
        # asyncssh tears the transport down asynchronously: there's a window
        # where `_conn` is set but `_transport` has already been cleared to
        # None. The previous check `self._conn._transport.is_closing()`
        # crashed with AttributeError in that window. We now defensively
        # treat a missing transport as a stale connection and reconnect.
        if self._conn is not None:
            transport = getattr(self._conn, "_transport", None)
            if transport is not None and not transport.is_closing():
                return self._conn
            # Stale: connection object lingers but the underlying transport
            # is gone or closing. Force-drop so we reconnect cleanly.
            self._conn = None

        # If we're resolver-backed, refresh the target host. The resolver's
        # internal cache (10s TTL) prevents thrashing — only the first call
        # after a cache miss does a real probe.
        if self._resolver is not None:
            active = await self._resolver.active()
            new_host = _extract_host(active)
            if new_host != self._host:
                logger.info(
                    "slate_ssh.host_switched",
                    from_=self._host, to=new_host,
                )
                self._host = new_host

        connect_kwargs: dict = {
            "port": self._port,
            "username": self._username,
            "known_hosts": None,  # LAN router, no realistic MITM
            # TCP-level keepalive: asyncssh sends SSH-layer pings every 30s
            # and tears the conn after 3 missed → stale connections after a
            # router reboot are detected within ~90s instead of relying on
            # the kernel's default 2h TCP timeout. See Bug E (2026-06-02).
            "keepalive_interval": 30,
            "keepalive_count_max": 3,
        }
        if self._private_key_pem is not None:
            private_key = asyncssh.import_private_key(self._private_key_pem)
            connect_kwargs["client_keys"] = [private_key]
            connect_kwargs["password"] = None
        else:
            connect_kwargs["password"] = self._password
            connect_kwargs["client_keys"] = None  # force password auth, never try local keys

        # Short retry with backoff: post-reboot, the Slate may accept TCP on
        # :22 before dropbear's auth subsystem is fully ready (asyncssh sees
        # a transient ConnectionLost / DisconnectError with empty message).
        # Two retries at 0.5s/1.5s cover the typical ~2s readiness window
        # without significantly slowing the legitimate "Slate truly down"
        # case (caller still sees an error within ~5s).
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                self._conn = await asyncio.wait_for(
                    asyncssh.connect(self._host, **connect_kwargs),
                    timeout=self._timeout,
                )
                break
            except (TimeoutError, asyncssh.Error, OSError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                # Final attempt failed — log with type + repr so the next
                # time we hit Bug-E-style empty error, we know what raised.
                logger.warning(
                    "slate_ssh.connect_failed",
                    host=self._host,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    error_repr=repr(exc),
                )
                # Tell the resolver that this URL just failed — next active()
                # call will re-probe instead of trusting the cache.
                if self._resolver is not None:
                    for candidate in self._resolver.candidates:
                        if _extract_host(candidate) == self._host:
                            await self._resolver.mark_failed(candidate)
                            break
                raise SlateSSHError(
                    f"SSH connect to {self._host} failed: {exc!r}"
                ) from exc
        # Defensive: loop should either set _conn or raise. assert helps
        # mypy + catches future logic changes.
        assert self._conn is not None, f"loop exited without conn (last={last_exc!r})"
        logger.info("slate_ssh.connected", host=self._host, auth_mode=self.auth_mode)
        return self._conn

    async def run(self, command: str, *, timeout: float | None = None) -> SSHResult:
        """Run a command, return stdout/stderr/exit. Raises on transport errors.

        Concurrency model: asyncssh supports many in-flight `conn.run()` calls
        on independent channels of the SAME connection — so we only hold the
        lock during the *connection acquisition* step. The command itself
        executes concurrently with other in-flight calls. This is what lets
        diag.collect_diag() fan its 7 probes out via asyncio.gather and shave
        ~20s off the wall time.

        Args:
            command: shell command to execute on the remote side.
            timeout: per-call override of the default 10s. Useful for long
                diagnostic dumps (`ubus call network.interface dump` can take
                3-4s by itself, and multi-step chained probes blow past 10s).
        """
        t = timeout if timeout is not None else self._timeout
        # Only the connection bootstrap is mutually-exclusive — channels run free.
        async with self._lock:
            conn = await self._ensure_connected()
        try:
            result = await asyncio.wait_for(conn.run(command, check=False), timeout=t)
        except (TimeoutError, asyncssh.Error, OSError, ConnectionError) as exc:
            # Drop the (likely-broken) connection so the next call reconnects.
            # OSError / BrokenPipeError can slip through asyncssh when the
            # remote tears the conn (router reboot). Re-acquire the lock for
            # the drop to avoid racing with other in-flight reconnects.
            async with self._lock:
                await self._drop_locked()
            raise SlateSSHError(
                f"SSH run {command!r} failed: {exc!r}"
            ) from exc

        stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout.decode() if result.stdout else "")
        stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr.decode() if result.stderr else "")
        return SSHResult(
            stdout=stdout,
            stderr=stderr,
            exit_status=int(result.exit_status or 0),
        )

    async def put_bytes(
        self, payload: bytes, remote_path: str, *, mode: int = 0o644
    ) -> None:
        """Upload raw bytes to `remote_path`. Atomic via .tmp + mv.

        Implementation: stream bytes via `cat > file` over a fresh SSH
        channel in binary mode (encoding=None). SFTP is unavailable on the
        Slate (dropbear ships without sftp-server), and busybox has no
        standalone `base64` binary — so heredoc tricks fail too. The stdin
        pipe is the most reliable cross-platform binary upload over SSH.

        Atomic semantics: write to `<remote>.tmp`, then `mv` onto the target.
        Original is preserved if the write fails mid-stream.
        """
        async with self._lock:
            conn = await self._ensure_connected()
        tmp_path = f"{remote_path}.tmp"
        # Quote remote paths to be safe against unexpected characters.
        # shlex.quote is overkill since we control the paths, but cheap insurance.
        import shlex
        q_tmp = shlex.quote(tmp_path)
        q_target = shlex.quote(remote_path)
        try:
            # 1. Stream the payload via stdin into a fresh temp file.
            r = await conn.run(
                f"cat > {q_tmp}", input=payload, encoding=None, check=False,
            )
            if r.exit_status != 0:
                raise SlateSSHError(
                    f"upload {remote_path!r}: cat > tmp returned exit={r.exit_status}"
                )
            # 2. chmod + atomic move.
            r2 = await conn.run(
                f"chmod {mode:o} {q_tmp} && mv {q_tmp} {q_target}",
                check=False,
            )
            if r2.exit_status != 0:
                # Try to clean up the tmp so we don't litter.
                await conn.run(f"rm -f {q_tmp}", check=False)
                raise SlateSSHError(
                    f"upload {remote_path!r}: chmod/mv exit={r2.exit_status}"
                )
        except (asyncssh.Error, OSError, ConnectionError) as exc:
            async with self._lock:
                await self._drop_locked()
            raise SlateSSHError(f"put_bytes {remote_path!r}: {exc!r}") from exc

    async def run_binary(
        self, command: str, *, timeout: float | None = None
    ) -> bytes:
        """Run `command` and return its stdout as raw bytes.

        Use when the remote process emits binary (e.g., `cat /dev/fb0`)
        that the default str-decoding path would corrupt.
        """
        t = timeout if timeout is not None else self._timeout
        async with self._lock:
            conn = await self._ensure_connected()
        try:
            r = await asyncio.wait_for(
                conn.run(command, encoding=None, check=False), timeout=t,
            )
        except (TimeoutError, asyncssh.Error, OSError, ConnectionError) as exc:
            async with self._lock:
                await self._drop_locked()
            raise SlateSSHError(f"run_binary {command!r}: {exc!r}") from exc
        if r.exit_status != 0:
            raise SlateSSHError(
                f"run_binary {command!r}: exit={r.exit_status}"
            )
        out = r.stdout
        if isinstance(out, str):  # asyncssh fallback
            out = out.encode("latin1")
        return bytes(out or b"")

    async def put_bytes_raw(
        self, payload: bytes, remote_path: str,
    ) -> None:
        """Stream bytes directly into `remote_path` — no .tmp + mv dance.

        Used for char devices like /dev/fb0 where atomic rename is not
        applicable. Bytes go via stdin to a remote `cat > path` in binary
        mode. Caller is responsible for the path being writable in-place.
        """
        async with self._lock:
            conn = await self._ensure_connected()
        import shlex
        cmd = f"cat > {shlex.quote(remote_path)}"
        try:
            r = await conn.run(cmd, input=payload, encoding=None, check=False)
        except (asyncssh.Error, OSError, ConnectionError) as exc:
            async with self._lock:
                await self._drop_locked()
            raise SlateSSHError(f"put_bytes_raw {remote_path!r}: {exc!r}") from exc
        if r.exit_status != 0:
            raise SlateSSHError(
                f"put_bytes_raw {remote_path!r}: exit={r.exit_status}"
            )

    async def close(self) -> None:
        async with self._lock:
            await self._drop_locked()

    async def _drop_locked(self) -> None:
        if self._conn is None:
            return
        with suppress(Exception):
            self._conn.close()
            await self._conn.wait_closed()
        self._conn = None
