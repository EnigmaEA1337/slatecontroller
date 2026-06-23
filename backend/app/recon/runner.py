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

from app.recon.discovery import (
    DiscoveredHost,
    arp_scan_layer2,
    fuse,
    ping_sweep,
    read_arp_cache,
)
from app.recon.interfaces import ReconInterface, list_active_interfaces
from app.recon.store import ReconStore
from app.recon.tcp import DEFAULT_PORTS, grab_banners, nmap_probe, probe_ports
from app.recon.tools import get_tool_status
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
    *,
    has_arp_scan: bool,
    has_nmap: bool,
    accumulated_open_ports: list[Any],
    accumulated_host_count_ref: list[int],
) -> None:
    """Drive ARP + ping + TCP probe on a single interface.

    When ``arp-scan`` is installed, the ARP discovery uses raw layer-2
    probes (catches silent hosts that never answer ICMP, finishes in
    1-2s on a /24 vs ~12s for the ping pool). When ``nmap`` is
    installed, the TCP+banner phase collapses into a single
    ``nmap -sV`` invocation (much faster + cleaner version banners).
    Both fall back to the busybox pipeline when the upgrade isn't
    present.

    Banner grab is deferred to the caller : it batches all ifaces'
    open ports together so we can show one progress line for it.
    """
    label = iface.name

    # ── 1) ARP discovery ──────────────────────────────────────────
    arp1: list[DiscoveredHost] = []
    if req.do_arp:
        if has_arp_scan:
            await store.set_progress(scan_id, f"arp-scan {label}")
            arp1 = await arp_scan_layer2(ssh, label, iface.scan_cidr)
            if not arp1:
                # arp-scan returned nothing : fall back to the
                # kernel ARP cache so we still get something.
                arp1 = await read_arp_cache(ssh, label)
        else:
            await store.set_progress(scan_id, f"ARP cache {label}")
            arp1 = await read_arp_cache(ssh, label)

    # ── 2) Ping sweep (skip when arp-scan already enumerated L2) ──
    pinged: list[str] = []
    skip_ping = has_arp_scan and bool(arp1)
    if req.do_ping and not skip_ping:
        async def _progress(done: int, total: int) -> None:
            await store.set_progress(
                scan_id, f"ping {label} {done}/{total}"
            )

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

    arp2: list[DiscoveredHost] = []
    if req.do_ping and req.do_arp and not skip_ping:
        # Re-read ARP after the sweep — many hosts that didn't answer
        # ICMP still got ARP-resolved by the ping attempt.
        await store.set_progress(scan_id, f"ARP {label} (post-sweep)")
        arp2 = await read_arp_cache(ssh, label)

    fused = fuse(arp1, pinged, arp2)
    extra_ips = {ip for ip in (iface.gateway, iface.slate_ip) if ip}
    have_ips = {h.ip for h in fused}
    for extra_ip in extra_ips - have_ips:
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

    # ── 3) TCP probe ──────────────────────────────────────────────
    if req.do_tcp and fused:
        target_ips = [h.ip for h in fused if h.ip != iface.slate_ip]
        if not target_ips:
            return
        if has_nmap:
            await store.set_progress(scan_id, f"nmap -sV {label} 0/{len(target_ips)}")
            pending_progress: list[asyncio.Task[None]] = []

            def _on_progress(done: int, total: int) -> None:
                pending_progress.append(asyncio.create_task(
                    store.set_progress(scan_id, f"nmap {label} {done}/{total}")
                ))

            opens = await nmap_probe(
                ssh, target_ips, on_progress=_on_progress,
            )
            if pending_progress:
                await asyncio.gather(*pending_progress, return_exceptions=True)
        else:
            await store.set_progress(scan_id, f"TCP {label} 0/{len(target_ips) * len(DEFAULT_PORTS)}")
            pending_progress = []

            def _on_progress(done: int, total: int) -> None:
                pending_progress.append(asyncio.create_task(
                    store.set_progress(scan_id, f"TCP {label} {done}/{total}")
                ))

            opens = await probe_ports(
                ssh, target_ips, on_progress=_on_progress,
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

        # Detect the optional toolchain once at scan start. If the
        # operator installed nmap/arp-scan via the settings page the
        # runner uses them transparently ; otherwise it falls back
        # to the busybox pipeline.
        tools = await get_tool_status(ssh)
        await store.set_progress(
            scan_id,
            f"engine : {'nmap' if tools.has_nmap else 'nc'} "
            f"+ {'arp-scan' if tools.has_arp_scan else 'ping'}",
        )

        open_ports: list[Any] = []
        host_count_ref = [0]
        for iface in ifaces:
            await _run_one_interface(
                ssh, store, scan_id, iface, req,
                has_arp_scan=tools.has_arp_scan,
                has_nmap=tools.has_nmap,
                accumulated_open_ports=open_ports,
                accumulated_host_count_ref=host_count_ref,
            )

        # nmap -sV already collected banners. Only run the busybox
        # banner pass when we used the fallback TCP probe.
        if req.do_banner and open_ports and not tools.has_nmap:
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
