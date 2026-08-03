"""Planification du scan quotidien (APScheduler, en process)."""

from __future__ import annotations

import logging

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings
from .providers.oddsapi import OddsAPIClient
from .services.scan import run_scan

logger = logging.getLogger(__name__)

SCAN_JOB_ID = "scan_quotidien"


def build_scheduler(client: httpx.AsyncClient, settings: Settings) -> AsyncIOScheduler:
    """Planifie le scan quotidien a l'heure configuree, dans le fuseau d'affichage."""
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def _job() -> None:
        logger.info("Scan planifie : demarrage")
        try:
            report = await run_scan(OddsAPIClient(client, settings), settings)
        except Exception:
            # Un scan qui echoue ne doit jamais tuer le process de l'application.
            logger.exception("Scan planifie : echec")
            return
        logger.info(
            "Scan planifie : %d evenements, cout %d credits",
            report.total_events,
            report.total_cost,
        )

    scheduler.add_job(
        _job,
        trigger=CronTrigger(
            hour=settings.scan_hour, minute=settings.scan_minute, timezone=settings.tz
        ),
        id=SCAN_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    logger.info(
        "Scan quotidien planifie a %02d:%02d %s",
        settings.scan_hour,
        settings.scan_minute,
        settings.tz,
    )
    return scheduler
