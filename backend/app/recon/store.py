"""DB store for recon scans + their child host/port rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ReconHostRow, ReconPortRow, ReconScanRow

# Status values exposed in the API.
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class ReconStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ---------------------------- create ---------------------------- #

    async def create(
        self,
        *,
        device_slug: str,
        scope: dict[str, Any],
    ) -> ReconScanRow:
        async with self._sf() as session:
            row = ReconScanRow(
                device_slug=device_slug,
                status=STATUS_RUNNING,
                scope_json=json.dumps(scope, ensure_ascii=True)[:2048],
                progress="préparation",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    # ---------------------------- updates ---------------------------- #

    async def set_progress(self, scan_id: int, progress: str) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(ReconScanRow).where(ReconScanRow.id == scan_id)
            )
            if row is None:
                return
            row.progress = progress[:256]
            await session.commit()

    async def mark_done(
        self,
        scan_id: int,
        *,
        host_count: int,
        port_count: int,
    ) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(ReconScanRow).where(ReconScanRow.id == scan_id)
            )
            if row is None:
                return
            row.status = STATUS_DONE
            row.progress = "terminé"
            row.finished_at = datetime.now(UTC)
            row.host_count = host_count
            row.port_count = port_count
            row.error = ""
            await session.commit()

    async def mark_failed(self, scan_id: int, error: str) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(ReconScanRow).where(ReconScanRow.id == scan_id)
            )
            if row is None:
                return
            row.status = STATUS_FAILED
            row.progress = "échec"
            row.error = error[:512]
            row.finished_at = datetime.now(UTC)
            await session.commit()

    async def mark_cancelled(self, scan_id: int) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(ReconScanRow).where(ReconScanRow.id == scan_id)
            )
            if row is None or row.status != STATUS_RUNNING:
                return
            row.status = STATUS_CANCELLED
            row.progress = "annulé"
            row.finished_at = datetime.now(UTC)
            await session.commit()

    # ---------------------------- host writes ---------------------------- #

    async def upsert_hosts(
        self,
        scan_id: int,
        interface: str,
        hosts: list[dict[str, Any]],
        *,
        gateway: str = "",
        slate_ip: str = "",
    ) -> None:
        """Replace the host rows for ``(scan_id, interface)`` with ``hosts``.

        We delete + re-insert rather than UPSERT because the runner
        always sweeps a whole interface in one shot, and the host list
        is the union of ARP + ping pickups — recomputing it is the
        authoritative state.
        """
        async with self._sf() as session:
            await session.execute(
                delete(ReconHostRow).where(
                    ReconHostRow.scan_id == scan_id,
                    ReconHostRow.interface == interface,
                )
            )
            for h in hosts:
                ip = h["ip"]
                session.add(
                    ReconHostRow(
                        scan_id=scan_id,
                        interface=interface,
                        ip=ip,
                        mac=h.get("mac", "")[:17],
                        vendor=h.get("vendor", "")[:128],
                        hostname=h.get("hostname", "")[:255],
                        source=h.get("source", "")[:16],
                        is_gateway=bool(gateway) and ip == gateway,
                        is_self=bool(slate_ip) and ip == slate_ip,
                    )
                )
            await session.commit()

    async def upsert_ports(self, scan_id: int, ports: list[dict[str, Any]]) -> None:
        """Replace ALL port rows of this scan with ``ports``. Idempotent."""
        async with self._sf() as session:
            await session.execute(
                delete(ReconPortRow).where(ReconPortRow.scan_id == scan_id)
            )
            for p in ports:
                session.add(
                    ReconPortRow(
                        scan_id=scan_id,
                        ip=p["ip"],
                        port=int(p["port"]),
                        state=p.get("state", "open")[:16],
                        banner=p.get("banner", "")[:512],
                        service=p.get("service", "")[:32],
                    )
                )
            await session.commit()

    # ---------------------------- reads ---------------------------- #

    async def get(self, scan_id: int) -> ReconScanRow | None:
        async with self._sf() as session:
            return await session.scalar(
                select(ReconScanRow).where(ReconScanRow.id == scan_id)
            )

    async def list(
        self,
        device_slug: str,
        *,
        limit: int = 50,
    ) -> list[ReconScanRow]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(ReconScanRow)
                        .where(ReconScanRow.device_slug == device_slug)
                        .order_by(ReconScanRow.started_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def hosts_for(self, scan_id: int) -> list[ReconHostRow]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(ReconHostRow)
                        .where(ReconHostRow.scan_id == scan_id)
                        .order_by(ReconHostRow.interface, ReconHostRow.ip)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def ports_for(self, scan_id: int) -> list[ReconPortRow]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(ReconPortRow)
                        .where(ReconPortRow.scan_id == scan_id)
                        .order_by(ReconPortRow.ip, ReconPortRow.port)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def delete(self, scan_id: int) -> bool:
        async with self._sf() as session:
            row = await session.scalar(
                select(ReconScanRow).where(ReconScanRow.id == scan_id)
            )
            if row is None:
                return False
            # Hosts and ports cascade via FK ondelete=CASCADE.
            await session.execute(
                delete(ReconScanRow).where(ReconScanRow.id == scan_id)
            )
            await session.commit()
            return True
