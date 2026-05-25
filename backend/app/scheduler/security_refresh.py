"""Scheduled jobs for the Security Device Status feature.

For now: refresh the three full-dataset exploit sources (CISA KEV,
Exploit-DB CSV, Metasploit metadata) once a day. The view-time join in
`/findings` then surfaces the fresh data on the next page load without
re-scanning the Slate.

A periodic full re-scan is intentionally NOT scheduled: scanning hits the
Slate's SSH for 4-5 minutes and re-queries OSV.dev for ~500 packages.
That's a deliberate-action operation, not background noise. The user can
trigger it from /security when they want.
"""

from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.security.exploit_enricher import ExploitEnricher

logger = structlog.get_logger(__name__)


async def _refresh_sources(enricher: ExploitEnricher) -> None:
    """One job wrapper around `enricher.refresh_all()` with logging."""
    logger.info("scheduler.security.refresh_sources.start")
    result = await enricher.refresh_all()
    logger.info("scheduler.security.refresh_sources.done", **result)


def register_security_jobs(
    scheduler: AsyncIOScheduler, enricher: ExploitEnricher
) -> None:
    """Wire all security-related scheduled jobs into the given scheduler."""
    scheduler.add_job(
        _refresh_sources,
        CronTrigger(hour=6, minute=0),
        args=[enricher],
        id="security_sources_refresh",
        name="Security: refresh KEV/ExploitDB/Metasploit",
        replace_existing=True,
        misfire_grace_time=3600,  # tolerate up to 1h late firing
    )
    logger.info("scheduler.security.jobs_registered", jobs=["security_sources_refresh"])
