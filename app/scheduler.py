"""APScheduler setup — register one job per enabled source."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from monitor.worker import Worker

logger = logging.getLogger(__name__)


def setup_scheduler(worker: Worker, sources_config: dict) -> AsyncIOScheduler:
    """Create an AsyncIOScheduler with one interval job per enabled source.

    Caller is responsible for starting the scheduler.
    """
    scheduler = AsyncIOScheduler()
    sources: dict[str, Any] = (sources_config or {}).get("sources", {}) or {}

    for source_id, cfg in sources.items():
        if not cfg.get("enabled", False):
            continue
        interval = int(cfg.get("poll_interval_sec", 600))
        scheduler.add_job(
            worker.run_once,
            trigger="interval",
            seconds=interval,
            id=f"poll_{source_id}",
            kwargs={"source_id": source_id},
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Scheduled %s every %ds", source_id, interval)

    return scheduler
