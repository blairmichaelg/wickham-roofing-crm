"""
ARQ worker settings.

Replaces Celery/RQ configuration with ARQ — an async-native task queue
that is dramatically lighter on Redis command usage (critical for
managed Redis services with per-command billing).

This module defines the WorkerSettings class that ARQ uses to
discover tasks, configure Redis connections, and set job defaults.
"""

import asyncio

import structlog
from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from app.core.backup import backup_database
from app.core.cleanup import cleanup_orphaned_artifacts
from app.workers.commission_processor import process_commission
from app.workers.escalation_processor import process_escalation
from app.workers.inspection_processor import process_inspection
from app.workers.photo_processor import process_photo_damage
from app.workers.rebuttal_processor import process_rebuttal
from app.workers.retail_quote_processor import process_retail_quote
from app.workers.storm_worker import ingest_storm_events
from app.workers.supplement_processor import process_supplement_event

logger = structlog.get_logger("app.workers.settings")


def get_redis_settings() -> RedisSettings:
    """
    Parse the REDIS_URL into ARQ's RedisSettings object.

    Supports both redis:// and rediss:// (TLS) connection strings.
    """
    settings = get_settings()
    url = settings.redis_url

    # Parse the URL into components for ARQ's RedisSettings
    # ARQ uses its own RedisSettings rather than a raw URL string
    if url.startswith("rediss://"):
        # TLS connection (Upstash, some managed providers)
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return RedisSettings(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6380,
            password=parsed.password,
            ssl=True,
            database=0 if settings.app_env.lower() == "prod" else 1,
        )
    else:
        # Standard connection (Render internal KV, local dev)
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return RedisSettings(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            password=parsed.password,
            database=0 if settings.app_env.lower() == "prod" else 1,
        )


async def startup(ctx: dict) -> None:
    """
    ARQ worker startup hook.
    """
    settings = get_settings()
    if settings.app_env.lower() == "prod":
        logger.info("[PROD MODE] Worker connected to Redis DB 0")
    else:
        logger.info("[DEV MODE] Worker connected to Redis DB 1")
    logger.info("worker_starting_up")
    
    # Run storm ingestion on startup in the background
    asyncio.create_task(ingest_storm_events(ctx))


async def shutdown(ctx: dict) -> None:
    """
    ARQ worker shutdown hook.
    """
    logger.info("worker_shutting_down")
    logger.info("worker_stopped")


async def run_cleanup(ctx: dict) -> None:
    """
    Nightly cron job to clean up orphaned temporary files.
    """
    logger.info("cron_cleanup_started")
    await asyncio.to_thread(cleanup_orphaned_artifacts)
    logger.info("cron_cleanup_finished")

async def run_backup(ctx: dict) -> None:
    """
    Cron job to backup the SQLite database safely using the native backup API.
    """
    logger.info("cron_backup_started")
    await asyncio.to_thread(backup_database, 14)
    logger.info("cron_backup_finished")


class WorkerSettings:
    """
    ARQ worker configuration.
    ARQ discovers this class by name when started via:
        arq app.workers.settings.WorkerSettings
    """

    # Coroutine function references (NOT dotted strings).
    # ARQ uses __qualname__ (e.g. "process_supplement_event") as the
    # dispatch key, which matches enqueue_job() call sites exactly.
    functions = [
        process_supplement_event,
        process_inspection,
        process_rebuttal,
        process_retail_quote,
        process_commission,
        process_escalation,
        process_photo_damage,
        ingest_storm_events,
    ]

    redis_settings = get_redis_settings()
    max_jobs = 10
    job_timeout = 1800
    max_tries = 3
    health_check_interval = 60
    on_startup = startup
    on_shutdown = shutdown

    # Dynamic minutes set based on storm_ingest_interval_minutes
    _settings = get_settings()
    _interval = _settings.storm_ingest_interval_minutes or 15
    _minutes_set = set(range(0, 60, _interval))

    cron_jobs = [
        cron(run_cleanup, hour=2, minute=0),
        cron(run_backup, hour={0, 4, 8, 12, 16, 20}, minute=0),
        cron(ingest_storm_events, minute=_minutes_set),
    ]
