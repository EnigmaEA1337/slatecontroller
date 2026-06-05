"""Ambient WiFi scan scheduler (Q2-A).

For each ``(device_slug, band)`` row in ``ambient_scan_configs`` where
``enabled=True``, this manager registers an APScheduler IntervalTrigger
job that :

  1. acquires the device's SSH bundle via the registry,
  2. runs a single-pass ``scan_band``,
  3. persists the result as a ``scan_history`` row with ``source="ambient"``,
  4. records ``last_run_at`` / ``last_status`` / ``last_error`` on the
     config row so the UI shows health without inspecting logs.

The manager is *idempotent* : ``reconfigure(slug, band, ...)`` removes
the previous job (if any) before scheduling the new one, so toggling
``enabled`` or changing ``interval_s`` from the UI just works.

A daily cleanup job (``ambient_scan_cleanup``) runs at 03:00 UTC and
purges ``scan_history`` rows where ``source="ambient"`` AND
``started_at < (now - retention_days)``. Manual scans are never
touched — they're the operator's deliberate work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    AmbientScanConfigRow,
    DeviceLocationRow,
    ScanHistoryRow,
    ScanNeighborRow,
)
from app.devices.registry import DeviceConnectionsRegistry
from app.wifi.models import WifiBand
from app.wifi.scanner import scan_band

logger = structlog.get_logger(__name__)


_JOB_PREFIX = "ambient_scan"
_CLEANUP_JOB_ID = "ambient_scan_cleanup"


def _job_id(slug: str, band: str) -> str:
    """Deterministic APScheduler job id for one (device, band) pair."""
    return f"{_JOB_PREFIX}__{slug}__{band}"


class AmbientScanManager:
    """Owns the lifecycle of all ambient-scan jobs across devices.

    A single instance lives on ``app.state.ambient_scan_manager``. Routes
    that mutate config call :meth:`reconfigure` so the scheduler picks up
    the change immediately ; the lifespan hook calls :meth:`register_all`
    once at boot to schedule whatever was enabled before the last shutdown.
    """

    def __init__(
        self,
        *,
        scheduler: AsyncIOScheduler,
        session_factory: async_sessionmaker,
        device_registry: DeviceConnectionsRegistry,
    ) -> None:
        self._scheduler = scheduler
        self._sf = session_factory
        self._dev_registry = device_registry

    async def register_all(self) -> None:
        """Boot-time : schedule a job for every enabled config row.

        Also wires the daily cleanup job. Safe to call multiple times —
        APScheduler replaces by id.
        """
        async with self._sf() as s:
            rows = (await s.scalars(
                select(AmbientScanConfigRow).where(
                    AmbientScanConfigRow.enabled.is_(True),
                ),
            )).all()
        for r in rows:
            self._schedule_one(r.device_slug, r.band, r.interval_s)
        self._scheduler.add_job(
            self._run_cleanup,
            CronTrigger(hour=3, minute=0),
            id=_CLEANUP_JOB_ID,
            name="Ambient scan: retention cleanup",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "ambient_scan.scheduler.bootstrapped",
            registered_jobs=len(rows),
        )

    def reconfigure(self, slug: str, band: str, *, enabled: bool, interval_s: int) -> None:
        """(Re)schedule or unschedule one (slug, band) tuple.

        Called by the route after a config UPSERT. Synchronous because
        APScheduler's job management API is sync.
        """
        jid = _job_id(slug, band)
        existing = self._scheduler.get_job(jid)
        if existing is not None:
            self._scheduler.remove_job(jid)
        if enabled:
            self._schedule_one(slug, band, interval_s)
            logger.info(
                "ambient_scan.scheduled", slug=slug, band=band, interval_s=interval_s,
            )
        else:
            logger.info("ambient_scan.unscheduled", slug=slug, band=band)

    def _schedule_one(self, slug: str, band: str, interval_s: int) -> None:
        self._scheduler.add_job(
            self._run_pass,
            IntervalTrigger(seconds=interval_s),
            args=[slug, band],
            id=_job_id(slug, band),
            name=f"Ambient scan: {slug} · {band} GHz",
            replace_existing=True,
            misfire_grace_time=interval_s,  # tolerate one missed tick
            max_instances=1,  # never overlap our own scans on the same band
            coalesce=True,
        )

    async def run_now(self, slug: str, band: str) -> dict[str, Any]:
        """Trigger one pass immediately, bypass scheduler. For test/UI button."""
        return await self._run_pass(slug, band)

    async def _run_pass(self, slug: str, band: str) -> dict[str, Any]:
        """The job body — one ambient scan + persistence + heartbeat write."""
        band_typed: WifiBand = band  # type: ignore[assignment]
        started_at_dt = datetime.now(UTC).replace(tzinfo=None)
        try:
            conn = await self._dev_registry.for_slug(slug)
        except Exception as exc:  # noqa: BLE001
            await self._mark_heartbeat(slug, band, status="error", err=f"registry: {exc}")
            logger.warning(
                "ambient_scan.skip_no_device", slug=slug, band=band, error=str(exc),
            )
            return {"status": "error", "reason": "no_device"}

        try:
            result = await scan_band(conn.ssh, band_typed)
        except Exception as exc:  # noqa: BLE001
            await self._mark_heartbeat(slug, band, status="error", err=f"scan: {exc}")
            logger.warning(
                "ambient_scan.failed", slug=slug, band=band, error=str(exc),
            )
            return {"status": "error", "reason": "scan_failed"}

        # Stamp the device's current location on the scan, identical to
        # what the manual /scan route does. Best-effort.
        loc = await self._latest_location_for(slug)

        async with self._sf() as s:
            run = ScanHistoryRow(
                device_slug=slug,
                band=band, iface=result.iface,
                started_at=started_at_dt,
                duration_s=result.duration_s,
                lat=loc[0] if loc else None,
                lon=loc[1] if loc else None,
                accuracy_m=loc[2] if loc else None,
                source="ambient",
                neighbors_count=len(result.neighbors),
                threats_count=0,  # ambient skips threat detection (cheap path)
                recommended_channel=result.recommended_channel,
                current_channel=result.current_channel,
                note="",
            )
            s.add(run)
            await s.flush()
            for n in result.neighbors:
                s.add(ScanNeighborRow(
                    scan_id=run.id, bssid=n.bssid, ssid=n.ssid,
                    hidden=n.hidden, channel=n.channel, band=n.band,
                    rssi_dbm=n.rssi_dbm, security=n.security,
                    ht_mode=n.ht_mode, is_wps_enabled=n.is_wps_enabled,
                    ap_root=n.ap_root, vendor=n.vendor,
                    vendor_slug=n.vendor_slug,
                    is_randomized=n.is_randomized,
                    seen_count=n.seen_count,
                    rssi_max=n.rssi_max or n.rssi_dbm,
                    rssi_min=n.rssi_min or n.rssi_dbm,
                    first_seen_offset_s=n.first_seen_offset_s,
                    last_seen_offset_s=n.last_seen_offset_s,
                ))
            await s.commit()
            scan_id = run.id

        await self._mark_heartbeat(slug, band, status="ok", err="")
        logger.info(
            "ambient_scan.persisted",
            slug=slug, band=band, scan_id=scan_id,
            neighbors=len(result.neighbors),
            duration_s=round(result.duration_s, 2),
        )
        return {
            "status": "ok",
            "scan_id": scan_id,
            "neighbors": len(result.neighbors),
        }

    async def _latest_location_for(
        self, slug: str,
    ) -> tuple[float, float, float | None] | None:
        try:
            async with self._sf() as s:
                row = await s.scalar(
                    select(DeviceLocationRow)
                    .where(DeviceLocationRow.device_slug == slug)
                    .order_by(DeviceLocationRow.created_at.desc())
                    .limit(1),
                )
                if row is None:
                    return None
                return row.lat, row.lon, row.accuracy_m
        except Exception:  # noqa: BLE001
            return None

    async def _mark_heartbeat(
        self, slug: str, band: str, *, status: str, err: str,
    ) -> None:
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            async with self._sf() as s:
                row = await s.scalar(
                    select(AmbientScanConfigRow).where(
                        AmbientScanConfigRow.device_slug == slug,
                        AmbientScanConfigRow.band == band,
                    ),
                )
                if row is None:
                    return
                row.last_run_at = now
                row.last_status = status
                row.last_error = err[:512]
                await s.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ambient_scan.heartbeat_write_failed",
                slug=slug, band=band, error=str(exc),
            )

    async def _run_cleanup(self) -> None:
        """Daily : drop ambient scan_history rows older than retention_days.

        Per-band retention is honoured : we read each enabled config and
        delete past its cutoff. Configs with ``enabled=False`` are skipped
        (the operator may want to keep the data they collected).
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        total_purged = 0
        try:
            async with self._sf() as s:
                rows = (await s.scalars(
                    select(AmbientScanConfigRow).where(
                        AmbientScanConfigRow.enabled.is_(True),
                    ),
                )).all()
                for r in rows:
                    cutoff = now - timedelta(days=r.retention_days)
                    # CASCADE on FK takes care of scan_neighbors.
                    result = await s.execute(
                        delete(ScanHistoryRow).where(
                            ScanHistoryRow.device_slug == r.device_slug,
                            ScanHistoryRow.band == r.band,
                            ScanHistoryRow.source == "ambient",
                            ScanHistoryRow.started_at < cutoff,
                        ),
                    )
                    total_purged += result.rowcount or 0
                await s.commit()
            logger.info(
                "ambient_scan.cleanup.done", purged=total_purged,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ambient_scan.cleanup.failed", error=str(exc),
            )
