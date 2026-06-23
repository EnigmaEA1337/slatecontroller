"""Async runner for one recon scan.

Orchestrates the per-interface sweep : ARP → ping → ARP again → fuse
→ persist hosts → TCP probe → banner grab → persist ports → mark done.

The runner is a single asyncio.Task spawned from the API route and
tracked in :class:`ReconTaskRegistry` so the operator can cancel a
running scan (cancellation just calls ``Task.cancel()``).

Progress is written incrementally to the ``recon_scans.progress``
column via :class:`ReconStore` so a polling client sees live updates.
Errors are caught at the runner level and persisted to the scan row
(status=failed + error message) — the operator can read the failure
in the UI instead of having to look at server logs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.recon.discovery import fuse, ping_sweep, read_arp_cache
from app.recon.interfaces import ReconInterface, list_active_interfaces
from app.recon.store import ReconStore
from app.recon.tcp import DEFAULT_PORTS, grab_banners, probe_ports
from app.slate.ssh import SlateSSH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanRequest:
    """What the operator asked for, captured at launch."""

    interfaces: list[str]
    do_arp: bool
    do_ping: bool
    do_tcp: bool
    do_banner: bool


class ReconTaskRegistry:
    """Map ``scan_id → asyncio.Task`` so the route can cancel a run."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def register(self, scan_id: int, task: asyncio.Task[None]) -> None:
        self._tasks[scan_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(scan_id, None))

    def cancel(self, scan_id: int) -> bool:
        task = self._tasks.get(scan_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


def _select_interfaces(
    available: list[ReconInterface], picked: list[str],
) -> list[ReconInterface]:
    """Resolve operator-picked names against the live interface list.

    Unknown names are silently dropped — the runner is robust to stale
    UI selections (the operator might've kept an old scan window open
    after a profile switch that tore down a bridge).
    """
    by_name = {i.name: i for i in available}
    out = [by_name[n] for n in picked if n in by_name]
    return [i for i in out if i.scannable]


async def _run_one_interface(
    ssh: SlateSSH,
    store: ReconStore,
    scan_id: int,
    iface: ReconInterface,
    req: ScanRequest,
    accumulated_open_ports: list[Any],
    accumulated_host_count_ref: list[int],
) -> None:
    """Drive ARP + ping + TCP probe on a single interface.

    Banner grab is deferred to the caller : it batches all ifaces'
    open ports together so we can show one progress line for it.
    """
    label = iface.name
    if req.do_arp:
        await store.set_progress(scan_id, f"ARP {label}")
    arp1 = await read_arp_cache(ssh, label) if req.do_arp else []

    pinged: list[str] = []
    if req.do_ping:
        async def _progress(done: int, total: int) -> None:
            await store.set_progress(
                scan_id, f"ping {label} {done}/{total}"
            )

        # ping_sweep takes a sync callback ; wrap into a coroutine
        # we schedule via asyncio.create_task to avoid blocking the
        # sweep on DB writes.
        pending_progress: list[asyncio.Task[None]] = []

        def _on_progress(done: int, total: int) -> None:
            pending_progress.append(asyncio.create_task(_progress(done, total)))

        pinged = await ping_sweep(
            ssh,
            label,
            iface.scan_cidr,
            iface.slate_ip,
            on_progress=_on_progress,
        )
        if pending_progress:
            await asyncio.gather(*pending_progress, return_exceptions=True)

    arp2: list[Any] = []
    if req.do_ping and req.do_arp:
        # Re-read ARP after the sweep — many hosts that didn't answer
        # ICMP still got ARP-resolved by the ping attempt.
        await store.set_progress(scan_id, f"ARP {label} (post-sweep)")
        arp2 = await read_arp_cache(ssh, label)

    fused = fuse(arp1, pinged, arp2)
    # Always include the gateway and the slate IP itself in the host
    # list when known — they're trivially "discovered" from the
    # interface enumeration, no probe needed.
    extra_ips = {ip for ip in (iface.gateway, iface.slate_ip) if ip}
    have_ips = {h.ip for h in fused}
    for extra_ip in extra_ips - have_ips:
        from app.recon.discovery import DiscoveredHost  # local import to avoid cycle  # noqa: PLC0415

        fused.append(DiscoveredHost(ip=extra_ip, mac="", source="meta"))
    fused.sort(key=lambda h: tuple(int(o) for o in h.ip.split(".")))

    await store.upsert_hosts(
        scan_id,
        label,
        [{"ip": h.ip, "mac": h.mac, "source": h.source} for h in fused],
        gateway=iface.gateway,
        slate_ip=iface.slate_ip,
    )
    accumulated_host_count_ref[0] += len(fused)

    if req.do_tcp and fused:
        await store.set_progress(scan_id, f"TCP {label} 0/{len(fused) * len(DEFAULT_PORTS)}")

        pending_progress: list[asyncio.Task[None]] = []

        def _on_progress(done: int, total: int) -> None:
            pending_progress.append(asyncio.create_task(
                store.set_progress(scan_id, f"TCP {label} {done}/{total}")
            ))

        opens = await probe_ports(
            ssh,
            [h.ip for h in fused],
            on_progress=_on_progress,
        )
        if pending_progress:
            await asyncio.gather(*pending_progress, return_exceptions=True)
        accumulated_open_ports.extend(opens)


async def run_scan(
    ssh: SlateSSH,
    store: ReconStore,
    task_registry: ReconTaskRegistry,
    scan_id: int,
    req: ScanRequest,
) -> None:
    """Top-level entry point for one scan run.

    Called from the route as ``asyncio.create_task(run_scan(...))``,
    so any exception ends up in the task's exception — we catch it
    here and persist a failed status rather than letting the task
    die silently.
    """
    try:
        available = await list_active_interfaces(ssh)
        ifaces = _select_interfaces(available, req.interfaces)
        if not ifaces:
            await store.mark_failed(
                scan_id, "aucune interface scannable parmi les choix"
            )
            return

        open_ports: list[Any] = []
        host_count_ref = [0]
        for iface in ifaces:
            await _run_one_interface(
                ssh, store, scan_id, iface, req, open_ports, host_count_ref,
            )

        if req.do_banner and open_ports:
            await store.set_progress(
                scan_id, f"bannières 0/{len(open_ports)}"
            )
            pending_progress: list[asyncio.Task[None]] = []

            def _on_progress(done: int, total: int) -> None:
                pending_progress.append(asyncio.create_task(
                    store.set_progress(scan_id, f"bannières {done}/{total}")
                ))

            open_ports = await grab_banners(ssh, open_ports, on_progress=_on_progress)
            if pending_progress:
                await asyncio.gather(*pending_progress, return_exceptions=True)

        if open_ports:
            await store.upsert_ports(
                scan_id,
                [
                    {
                        "ip": p.ip,
                        "port": p.port,
                        "state": p.state,
                        "banner": p.banner,
                        "service": p.service,
                    }
                    for p in open_ports
                ],
            )

        await store.mark_done(
            scan_id,
            host_count=host_count_ref[0],
            port_count=len(open_ports),
        )
    except asyncio.CancelledError:
        await store.mark_cancelled(scan_id)
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("recon scan %d failed", scan_id)
        await store.mark_failed(scan_id, str(exc))
