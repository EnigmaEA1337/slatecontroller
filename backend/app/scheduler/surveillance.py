"""Surveillance session scheduler (Q2-C).

A "session" is a named, time-bounded period of intensive scanning.
Start one from the UI (or REST), and per the chosen bands :

  - one APScheduler ``IntervalTrigger`` job per (session, band) runs
    ``scan_band()`` every ``interval_s`` seconds,
  - each pass is persisted as a regular ``scan_history`` row with
    ``session_id`` set so the timeline analytics can reach them,
  - the session's ``total_passes`` / ``unique_bssids`` rolling counters
    are updated after every pass,
  - when ``started_at + target_duration_s`` is reached, the jobs
    auto-remove themselves and the session goes to ``status="completed"``.

The whole thing is restartable : on backend boot the manager re-attaches
every ``status="active"`` session whose deadline hasn't passed.

Timeline analytics (``timeline_for``) bucket time into ``num_buckets``
columns and emit per-BSSID rows with :

  - presence_ratio : fraction of passes the BSSID was observed in,
  - rssi_drift    : ``rssi_max - rssi_min`` across the session,
  - classification :
      * stable    : presence ≥ 0.8 and drift < 5 dB     (fixed infra)
      * edge      : presence ≥ 0.5 and drift ≥ 5 dB     (fixed marginal)
      * drifting  : 0.2 ≤ presence < 0.5                (in/out periodically)
      * transient : presence < 0.2                      (passing device)

This is what makes a 2-hour session different from "200 single scans" —
the classification + presence bar visualises *behaviour over time*.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    DeviceLocationRow,
    ScanHistoryRow,
    ScanNeighborRow,
    SurveillanceSessionRow,
)
from app.devices.registry import DeviceConnectionsRegistry
from app.wifi.models import WifiBand
from app.wifi.scanner import scan_band

logger = structlog.get_logger(__name__)


_JOB_PREFIX = "surveillance"
_SUPERVISOR_JOB_ID = "surveillance_supervisor"
# How often the supervisor checks whether active sessions should finish.
_SUPERVISOR_INTERVAL_S = 30


def _job_id(session_id: int, band: str) -> str:
    return f"{_JOB_PREFIX}__{session_id}__{band}"


def _parse_bands(csv: str) -> list[WifiBand]:
    """Validate + split the comma-separated bands string."""
    out: list[WifiBand] = []
    for raw in csv.split(","):
        b = raw.strip()
        if b in ("2", "5", "6"):
            out.append(b)  # type: ignore[arg-type]
    return out


class SurveillanceManager:
    """Owns the lifecycle of every active surveillance session's jobs.

    Lives on ``app.state.surveillance_manager``. The mutating routes call
    :meth:`start_session` / :meth:`cancel_session` ; the supervisor runs
    every 30s to finalize sessions whose deadline has passed.
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
        """Boot : re-schedule every ``active`` session whose deadline is
        in the future. Sessions whose deadline already passed are
        finalized to ``completed`` on the spot."""
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sf() as s:
            rows = (await s.scalars(
                select(SurveillanceSessionRow).where(
                    SurveillanceSessionRow.status == "active",
                ),
            )).all()
            re_scheduled = 0
            finalized = 0
            for row in rows:
                deadline = row.started_at + timedelta(
                    seconds=row.target_duration_s,
                )
                if deadline <= now:
                    row.status = "completed"
                    row.ended_at = now
                    finalized += 1
                    continue
                for band in _parse_bands(row.bands):
                    self._schedule_one(row.id, band, row.interval_s)
                    re_scheduled += 1
            await s.commit()
        self._scheduler.add_job(
            self._run_supervisor,
            IntervalTrigger(seconds=_SUPERVISOR_INTERVAL_S),
            id=_SUPERVISOR_JOB_ID,
            name="Surveillance: deadline supervisor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "surveillance.bootstrapped",
            re_scheduled=re_scheduled, finalized=finalized,
        )

    async def start_session(
        self,
        *,
        slug: str,
        name: str,
        bands_csv: str,
        target_duration_s: int,
        interval_s: int,
        location_lat: float | None,
        location_lon: float | None,
        location_label: str,
        note: str,
    ) -> SurveillanceSessionRow:
        bands = _parse_bands(bands_csv)
        if not bands:
            raise ValueError(f"no valid band in {bands_csv!r}")
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sf() as s:
            row = SurveillanceSessionRow(
                device_slug=slug,
                name=name[:128] or f"Session {now.isoformat(timespec='seconds')}",
                status="active",
                started_at=now,
                target_duration_s=target_duration_s,
                interval_s=interval_s,
                bands=",".join(bands),
                location_lat=location_lat,
                location_lon=location_lon,
                location_label=location_label[:128],
                note=note[:1024],
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            session_id = row.id
        for band in bands:
            self._schedule_one(session_id, band, interval_s)
        logger.info(
            "surveillance.started",
            session_id=session_id, slug=slug, bands=row.bands,
            duration_s=target_duration_s, interval_s=interval_s,
        )
        return row

    async def cancel_session(self, session_id: int) -> None:
        async with self._sf() as s:
            row = await s.get(SurveillanceSessionRow, session_id)
            if row is None:
                return
            if row.status != "active":
                return
            row.status = "cancelled"
            row.ended_at = datetime.now(UTC).replace(tzinfo=None)
            await s.commit()
            bands = _parse_bands(row.bands)
        self._remove_jobs(session_id, bands)
        logger.info("surveillance.cancelled", session_id=session_id)

    def _schedule_one(
        self, session_id: int, band: WifiBand, interval_s: int,
    ) -> None:
        self._scheduler.add_job(
            self._run_pass,
            IntervalTrigger(seconds=interval_s),
            args=[session_id, band],
            id=_job_id(session_id, band),
            name=f"Surveillance session #{session_id} · {band} GHz",
            replace_existing=True,
            misfire_grace_time=interval_s,
            max_instances=1,
            coalesce=True,
        )

    def _remove_jobs(self, session_id: int, bands: list[WifiBand]) -> None:
        for band in bands:
            try:
                self._scheduler.remove_job(_job_id(session_id, band))
            except Exception:  # noqa: BLE001
                pass

    async def _run_pass(self, session_id: int, band: WifiBand) -> None:
        """Body of one surveillance scan pass."""
        try:
            async with self._sf() as s:
                sess = await s.get(SurveillanceSessionRow, session_id)
                if sess is None or sess.status != "active":
                    return  # supervisor will clean up the orphan job
                slug = sess.device_slug
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "surveillance.pass.session_lookup_failed",
                session_id=session_id, error=str(exc),
            )
            return

        try:
            conn = await self._dev_registry.for_slug(slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "surveillance.pass.no_device",
                session_id=session_id, slug=slug, error=str(exc),
            )
            return

        started_at_dt = datetime.now(UTC).replace(tzinfo=None)
        try:
            result = await scan_band(conn.ssh, band)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "surveillance.pass.scan_failed",
                session_id=session_id, band=band, error=str(exc),
            )
            return

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
                source="surveillance",
                neighbors_count=len(result.neighbors),
                threats_count=0,
                recommended_channel=result.recommended_channel,
                current_channel=result.current_channel,
                note="",
                session_id=session_id,
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

            sess = await s.get(SurveillanceSessionRow, session_id)
            if sess is not None and sess.status == "active":
                sess.total_passes = (sess.total_passes or 0) + 1
                # Recompute unique BSSIDs by SQL — cheap on indexed table.
                uniq = await s.scalar(
                    select(func.count(func.distinct(ScanNeighborRow.bssid)))
                    .join(
                        ScanHistoryRow,
                        ScanNeighborRow.scan_id == ScanHistoryRow.id,
                    )
                    .where(ScanHistoryRow.session_id == session_id),
                )
                sess.unique_bssids = int(uniq or 0)
            await s.commit()

    async def _run_supervisor(self) -> None:
        """Finalize sessions whose deadline has passed."""
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sf() as s:
            active = (await s.scalars(
                select(SurveillanceSessionRow).where(
                    SurveillanceSessionRow.status == "active",
                ),
            )).all()
            for row in active:
                deadline = row.started_at + timedelta(
                    seconds=row.target_duration_s,
                )
                if deadline <= now:
                    row.status = "completed"
                    row.ended_at = now
                    self._remove_jobs(row.id, _parse_bands(row.bands))
                    logger.info(
                        "surveillance.completed",
                        session_id=row.id,
                        passes=row.total_passes,
                        unique_bssids=row.unique_bssids,
                    )
            await s.commit()

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

    async def timeline_for(
        self, session_id: int, *, num_buckets: int = 80,
    ) -> dict[str, Any]:
        """Compute per-BSSID timeline + classification for the session.

        Returns a dict ready to ship to the UI :
        ``{
            session: {id, name, started_at, ended_at, total_passes, ...},
            buckets: [{index, start_offset_s, end_offset_s}, ...],
            rows: [
                {
                    bssid, ssid, vendor, channel, band, ap_root,
                    presence_ratio, rssi_drift, rssi_max, rssi_min,
                    classification,
                    buckets: [{idx, rssi_dbm | null}, ...],
                }, ...
            ],
        }``
        """
        async with self._sf() as s:
            sess = await s.get(SurveillanceSessionRow, session_id)
            if sess is None:
                return {}
            # Pull every neighbour observation linked to this session,
            # plus the scan's timestamp for time-bucket mapping.
            joined = await s.execute(
                select(
                    ScanNeighborRow.bssid,
                    ScanNeighborRow.ssid,
                    ScanNeighborRow.vendor,
                    ScanNeighborRow.vendor_slug,
                    ScanNeighborRow.channel,
                    ScanNeighborRow.band,
                    ScanNeighborRow.ap_root,
                    ScanNeighborRow.rssi_dbm,
                    ScanNeighborRow.is_randomized,
                    ScanHistoryRow.started_at,
                    ScanHistoryRow.id.label("scan_id"),
                )
                .join(
                    ScanHistoryRow,
                    ScanNeighborRow.scan_id == ScanHistoryRow.id,
                )
                .where(ScanHistoryRow.session_id == session_id)
                .order_by(ScanHistoryRow.started_at),
            )
            rows = joined.all()
            # Also need the list of distinct scan_ids = total passes.
            total_passes_q = await s.execute(
                select(func.count(ScanHistoryRow.id))
                .where(ScanHistoryRow.session_id == session_id),
            )
            total_passes = int(total_passes_q.scalar() or 0)

        # Time origin = session start. Window = duration so far (or target).
        end_time = sess.ended_at or datetime.now(UTC).replace(tzinfo=None)
        window_s = max(1.0, (end_time - sess.started_at).total_seconds())
        bucket_size_s = window_s / num_buckets

        # Per-BSSID accumulator. Key by bssid (not ap_root) so the user
        # sees individual VAPs — grouping by ap_root can be done client-side.
        accum: dict[str, dict[str, Any]] = {}
        scans_per_bssid: dict[str, set[int]] = {}
        for r in rows:
            bssid = r.bssid
            offset_s = (r.started_at - sess.started_at).total_seconds()
            idx = min(num_buckets - 1, max(0, int(offset_s / bucket_size_s)))
            slot = accum.get(bssid)
            if slot is None:
                slot = {
                    "bssid": bssid,
                    "ssid": r.ssid,
                    "vendor": r.vendor,
                    "vendor_slug": r.vendor_slug,
                    "channel": r.channel,
                    "band": r.band,
                    "ap_root": r.ap_root,
                    "is_randomized": r.is_randomized,
                    "rssi_max": r.rssi_dbm,
                    "rssi_min": r.rssi_dbm,
                    "buckets": {},
                }
                accum[bssid] = slot
                scans_per_bssid[bssid] = set()
            if r.rssi_dbm > slot["rssi_max"]:
                slot["rssi_max"] = r.rssi_dbm
            if r.rssi_dbm < slot["rssi_min"]:
                slot["rssi_min"] = r.rssi_dbm
            # Take the strongest RSSI per bucket — closer-to-the-device
            # blip dominates over a far echo within the same window.
            current = slot["buckets"].get(idx)
            if current is None or r.rssi_dbm > current:
                slot["buckets"][idx] = r.rssi_dbm
            scans_per_bssid[bssid].add(r.scan_id)
            # Latest text fields win — useful if the AP rebrands mid-session.
            slot["ssid"] = r.ssid
            slot["vendor"] = r.vendor
            slot["vendor_slug"] = r.vendor_slug

        def _classify(presence: float, drift: int) -> str:
            if presence >= 0.8 and drift < 5:
                return "stable"
            if presence >= 0.5 and drift >= 5:
                return "edge"
            if presence < 0.2:
                return "transient"
            return "drifting"

        out_rows: list[dict[str, Any]] = []
        for bssid, slot in accum.items():
            passes_seen = len(scans_per_bssid[bssid])
            presence = (passes_seen / total_passes) if total_passes else 0.0
            drift = slot["rssi_max"] - slot["rssi_min"]
            slot_buckets = [
                {"idx": i, "rssi_dbm": slot["buckets"].get(i)}
                for i in range(num_buckets)
            ]
            out_rows.append({
                "bssid": bssid,
                "ssid": slot["ssid"],
                "vendor": slot["vendor"],
                "vendor_slug": slot["vendor_slug"],
                "channel": slot["channel"],
                "band": slot["band"],
                "ap_root": slot["ap_root"],
                "is_randomized": slot["is_randomized"],
                "rssi_max": slot["rssi_max"],
                "rssi_min": slot["rssi_min"],
                "rssi_drift": drift,
                "passes_seen": passes_seen,
                "presence_ratio": round(presence, 3),
                "classification": _classify(presence, drift),
                "buckets": slot_buckets,
            })

        # Sort : strongest median RSSI first by default (closest first).
        out_rows.sort(
            key=lambda r: ((r["rssi_max"] + r["rssi_min"]) // 2),
            reverse=True,
        )

        return {
            "session": {
                "id": sess.id,
                "name": sess.name,
                "status": sess.status,
                "started_at": sess.started_at,
                "ended_at": sess.ended_at,
                "target_duration_s": sess.target_duration_s,
                "interval_s": sess.interval_s,
                "bands": sess.bands,
                "location_lat": sess.location_lat,
                "location_lon": sess.location_lon,
                "location_label": sess.location_label,
                "note": sess.note,
                "total_passes": total_passes,
                "unique_bssids": len(accum),
                "window_s": window_s,
            },
            "buckets": [
                {
                    "index": i,
                    "start_offset_s": i * bucket_size_s,
                    "end_offset_s": (i + 1) * bucket_size_s,
                }
                for i in range(num_buckets)
            ],
            "rows": out_rows,
        }
