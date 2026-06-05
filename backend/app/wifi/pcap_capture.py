"""LAN-side tcpdump capture orchestration.

Owns the lifecycle of one or many ``tcpdump`` processes running on the
Slate over SSH :

  - ensures ``tcpdump`` is installed (one-shot ``opkg install``),
  - kicks off ``timeout DURATION tcpdump …`` as a background process,
    storing the resulting pcap at ``/tmp/slate-ctrl-pcap-<id>.pcap``,
  - polls the running PID + the file size for the status endpoint,
  - returns the raw pcap bytes on download.

Phase 1 limitation : the MT7990 driver doesn't expose monitor mode,
so the iface picker is limited to L2/L3 interfaces (br-lan, eth0,
tailscale0…). No 802.11 raw frames — that's Phase 2 with a USB dongle.
"""

from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import PcapCaptureRow
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


# Caps + defaults — Phase 1 is intentionally conservative to keep the
# Slate's /tmp (tmpfs, 500 MB) safe.
MIN_DURATION_S = 5
MAX_DURATION_S = 300         # 5 min max per capture
MIN_SNAPLEN = 64
MAX_SNAPLEN = 65535
DEFAULT_SNAPLEN = 256
# Interfaces the operator can pick. We intentionally exclude the AP
# interfaces (ra*, rai*, rax*) here — pcap on an AP interface only
# sees frames addressed to/from the AP from the client's IP layer,
# not the raw 802.11 frames the operator probably expects. br-lan
# captures user traffic ; tailscale0 captures tunnel ingress/egress ;
# eth0 captures the WAN.
ALLOWED_IFACES: tuple[str, ...] = (
    "br-lan",
    "eth0",
    "tailscale0",
    "apcli0",
    "apclii0",
    "apclix0",
)


@dataclass(frozen=True)
class PcapStartSpec:
    iface: str
    duration_s: int
    snaplen: int
    filter_expr: str
    label: str


def _remote_path_for(capture_id: int) -> str:
    return f"/tmp/slate-ctrl-pcap-{capture_id}.pcap"


class PcapCaptureManager:
    """Stateless orchestrator — every request opens its own session."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list_for(self, slug: str) -> list[PcapCaptureRow]:
        async with self._sf() as s:
            rows = (await s.scalars(
                select(PcapCaptureRow)
                .where(PcapCaptureRow.device_slug == slug)
                .order_by(PcapCaptureRow.started_at.desc()),
            )).all()
            return list(rows)

    async def get(self, slug: str, capture_id: int) -> PcapCaptureRow | None:
        async with self._sf() as s:
            return await s.scalar(
                select(PcapCaptureRow).where(
                    PcapCaptureRow.id == capture_id,
                    PcapCaptureRow.device_slug == slug,
                ),
            )

    async def start(
        self,
        *,
        slug: str,
        ssh: SlateSSH,
        spec: PcapStartSpec,
    ) -> PcapCaptureRow:
        """Validate spec, ensure tcpdump installed, kick off the
        background tcpdump and return the new row."""
        if spec.iface not in ALLOWED_IFACES:
            raise ValueError(
                f"iface {spec.iface!r} not allowed (pick one of "
                f"{', '.join(ALLOWED_IFACES)})",
            )
        if not (MIN_DURATION_S <= spec.duration_s <= MAX_DURATION_S):
            raise ValueError(
                f"duration_s must be in [{MIN_DURATION_S}, {MAX_DURATION_S}]",
            )
        if not (MIN_SNAPLEN <= spec.snaplen <= MAX_SNAPLEN):
            raise ValueError(
                f"snaplen must be in [{MIN_SNAPLEN}, {MAX_SNAPLEN}]",
            )

        # Ensure tcpdump exists. opkg update + install is ~1s if cached,
        # ~10s on a fresh package list.
        try:
            check = await ssh.run("which tcpdump 2>/dev/null", timeout=5)
        except SlateSSHError as exc:
            raise RuntimeError(f"SSH check tcpdump failed: {exc}") from exc
        if not check.stdout.strip():
            logger.info("pcap.tcpdump.installing", slug=slug)
            try:
                await ssh.run(
                    "opkg update >/dev/null 2>&1; "
                    "opkg install tcpdump 2>&1 | tail -5",
                    timeout=60,
                )
            except SlateSSHError as exc:
                raise RuntimeError(
                    f"opkg install tcpdump failed: {exc}",
                ) from exc
            recheck = await ssh.run("which tcpdump 2>/dev/null", timeout=5)
            if not recheck.stdout.strip():
                raise RuntimeError(
                    "tcpdump not present after opkg install — "
                    "check Slate package feeds",
                )

        # Reserve the row first so we have a stable ID for the remote path.
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sf() as s:
            row = PcapCaptureRow(
                device_slug=slug,
                iface=spec.iface,
                duration_s=spec.duration_s,
                snaplen=spec.snaplen,
                filter_expr=spec.filter_expr,
                status="planned",
                started_at=now,
                label=spec.label[:128],
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            capture_id = row.id
        remote_path = _remote_path_for(capture_id)

        # Build the command. ``timeout`` is busybox-provided ; we trail
        # the BPF filter verbatim (already validated by tcpdump itself
        # on parse failure → exit non-zero → status=failed below).
        filter_part = (
            f" {spec.filter_expr}" if spec.filter_expr.strip() else ""
        )
        # `-Z root` keeps tcpdump from dropping privileges (busybox
        # tcpdump doesn't always create the `tcpdump` user). `-U` flushes
        # the buffer per packet so a polling status endpoint can see
        # accumulating bytes.
        #
        # **busybox quirk** (2026-06-05) : this firmware ships without
        # ``nohup`` / ``setsid`` / ``start-stop-daemon`` / ``disown``.
        # The portable replacement is a subshell + bg : ``( cmd ) &``
        # detaches the child from the SSH session's job control, and
        # ``< /dev/null`` cuts stdin so the shell can return immediately.
        # We capture the resulting PID via ``$!`` — busybox ash may
        # return either the subshell or the ``timeout`` PID depending
        # on internal optimisation, but either kills the tree on stop.
        log_path = f"/tmp/slate-ctrl-pcap-{capture_id}.log"
        cmd = (
            f"rm -f {shlex.quote(remote_path)} {shlex.quote(log_path)} ; "
            f"( timeout {spec.duration_s} tcpdump "
            f"-i {shlex.quote(spec.iface)} "
            f"-w {shlex.quote(remote_path)} "
            f"-s {spec.snaplen} -Z root -U"
            f"{filter_part} "
            f"< /dev/null > {shlex.quote(log_path)} 2>&1 ) & "
            f"echo $!"
        )
        try:
            r = await ssh.run(cmd, timeout=10)
        except SlateSSHError as exc:
            async with self._sf() as s:
                row2 = await s.get(PcapCaptureRow, capture_id)
                if row2 is not None:
                    row2.status = "failed"
                    row2.error = f"start failed: {exc}"[:512]
                    await s.commit()
            raise RuntimeError(f"start tcpdump failed: {exc}") from exc

        try:
            pid = int(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            async with self._sf() as s:
                row2 = await s.get(PcapCaptureRow, capture_id)
                if row2 is not None:
                    row2.status = "failed"
                    row2.error = f"could not parse pid from {r.stdout!r}"[:512]
                    await s.commit()
            raise RuntimeError("could not parse tcpdump pid") from exc

        async with self._sf() as s:
            row2 = await s.get(PcapCaptureRow, capture_id)
            if row2 is not None:
                row2.status = "running"
                row2.remote_path = remote_path
                row2.remote_pid = pid
                await s.commit()
                await s.refresh(row2)
                row = row2

        logger.info(
            "pcap.capture.started",
            slug=slug, capture_id=capture_id,
            iface=spec.iface, duration_s=spec.duration_s, pid=pid,
        )
        return row

    async def refresh_status(
        self, slug: str, ssh: SlateSSH, capture_id: int,
    ) -> PcapCaptureRow | None:
        """Re-query the Slate for the PID + file size, update the row.

        Cheap (one SSH command). The frontend polls this endpoint to
        drive a progress bar.
        """
        row = await self.get(slug, capture_id)
        if row is None:
            return None
        if row.status not in ("running", "planned"):
            return row
        if not row.remote_pid or not row.remote_path:
            return row
        # Detect "is tcpdump still writing the file" by walking ``ps`` and
        # matching both ``tcpdump`` and the pcap path on the same row.
        # We can't use ``pgrep -f path`` directly : pgrep's own cmdline
        # contains the path argument, so it self-matches and we'd report
        # RUNNING forever. The classic ``grep -v grep`` chain dodges
        # that by excluding any process whose cmdline contains "grep".
        # We also can't use ``kill -0 $remote_pid`` alone because
        # busybox's ``$!`` from a subshell sometimes refers to the
        # already-exited wrapper, leaving tcpdump orphaned to init.
        path = row.remote_path
        # ``stat`` is missing from this firmware's busybox (2026-06-05
        # live discovery, even on the stock GL.iNet image). ``wc -c <``
        # is the portable fallback : reads stdin, prints the byte count,
        # zero exit on success. The redirect ``< FILE`` short-circuits
        # the case where FILE doesn't exist (shell fails, no output).
        try:
            r = await ssh.run(
                f"ps w | grep tcpdump | grep -F {shlex.quote(path)} | "
                f"grep -v grep >/dev/null 2>&1 && echo RUNNING || echo DONE; "
                f"wc -c < {shlex.quote(path)} 2>/dev/null || echo 0",
                timeout=5,
            )
        except SlateSSHError as exc:
            logger.warning(
                "pcap.refresh.ssh_failed",
                slug=slug, capture_id=capture_id, error=str(exc),
            )
            return row
        lines = r.stdout.strip().splitlines()
        running = "RUNNING" in (lines[0] if lines else "")
        try:
            bytes_now = int(lines[1]) if len(lines) > 1 else 0
        except ValueError:
            bytes_now = 0

        async with self._sf() as s:
            row2 = await s.get(PcapCaptureRow, capture_id)
            if row2 is None:
                return None
            row2.bytes_captured = bytes_now
            if not running and row2.status == "running":
                row2.status = "completed"
                row2.ended_at = datetime.now(UTC).replace(tzinfo=None)
            await s.commit()
            await s.refresh(row2)
            return row2

    async def stop(self, slug: str, ssh: SlateSSH, capture_id: int) -> PcapCaptureRow | None:
        row = await self.get(slug, capture_id)
        if row is None:
            return None
        if row.status != "running" or not row.remote_pid:
            return row
        # Resolve the real tcpdump PIDs via ``ps`` (same anti-self-match
        # technique as refresh_status). We avoid ``pkill -f $path``
        # because pkill's own cmdline contains the path and busybox
        # would happily SIGTERM itself. busybox sleep doesn't accept
        # decimals — round up to 1s for the SIGKILL grace window.
        path = row.remote_path
        try:
            await ssh.run(
                # Find tcpdump PIDs writing to our path + the timeout
                # wrapper PID, then signal them. We also include the
                # original $! we captured at start as a defensive net.
                f"PIDS=$(ps w | grep tcpdump | grep -F {shlex.quote(path)} | "
                f"grep -v grep | awk '{{print $1}}'); "
                f"for p in $PIDS {row.remote_pid}; do kill $p 2>/dev/null; done; "
                f"sleep 1; "
                f"PIDS=$(ps w | grep tcpdump | grep -F {shlex.quote(path)} | "
                f"grep -v grep | awk '{{print $1}}'); "
                f"for p in $PIDS {row.remote_pid}; do kill -9 $p 2>/dev/null; done; true",
                timeout=10,
            )
        except SlateSSHError as exc:
            logger.warning("pcap.stop.ssh_failed", error=str(exc))
        async with self._sf() as s:
            row2 = await s.get(PcapCaptureRow, capture_id)
            if row2 is not None:
                row2.status = "cancelled"
                row2.ended_at = datetime.now(UTC).replace(tzinfo=None)
                await s.commit()
                await s.refresh(row2)
                return row2
        return row

    async def download(self, slug: str, ssh: SlateSSH, capture_id: int) -> bytes | None:
        """Pull the pcap binary from the Slate over SSH (base64 transport).

        Returns the raw bytes ready to ship as ``application/vnd.tcpdump.pcap``.
        ``None`` if the row doesn't exist or has no produced file.

        **busybox quirk** : neither ``base64`` nor ``stat`` ship with
        this firmware. We use ``openssl base64 -in FILE`` which is
        guaranteed present (used elsewhere by the agent). openssl
        wraps lines at 76 chars MIME-style, which Python's
        ``base64.b64decode`` accepts as-is.
        """
        row = await self.get(slug, capture_id)
        if row is None or not row.remote_path:
            return None
        try:
            r = await ssh.run(
                f"openssl base64 -in {shlex.quote(row.remote_path)} 2>/dev/null",
                timeout=30,
            )
        except SlateSSHError as exc:
            raise RuntimeError(f"SSH base64 download failed: {exc}") from exc
        try:
            return base64.b64decode(r.stdout)
        except Exception as exc:
            raise RuntimeError(f"base64 decode failed: {exc}") from exc

    async def delete(self, slug: str, ssh: SlateSSH, capture_id: int) -> bool:
        """Cancel if running, remove the remote pcap file, drop the row."""
        row = await self.get(slug, capture_id)
        if row is None:
            return False
        if row.status == "running":
            await self.stop(slug, ssh, capture_id)
        if row.remote_path:
            try:
                await ssh.run(
                    f"rm -f {shlex.quote(row.remote_path)} "
                    f"/tmp/slate-ctrl-pcap-{capture_id}.log",
                    timeout=5,
                )
            except SlateSSHError:
                pass
        async with self._sf() as s:
            row2 = await s.get(PcapCaptureRow, capture_id)
            if row2 is not None:
                await s.delete(row2)
                await s.commit()
        return True


def to_view(row: PcapCaptureRow) -> dict[str, Any]:
    """Plain dict shape returned by the API (no Pydantic to keep it simple)."""
    return {
        "id": row.id,
        "iface": row.iface,
        "duration_s": row.duration_s,
        "snaplen": row.snaplen,
        "filter_expr": row.filter_expr,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "bytes_captured": row.bytes_captured,
        "remote_path": row.remote_path,
        "error": row.error,
        "label": row.label,
    }
