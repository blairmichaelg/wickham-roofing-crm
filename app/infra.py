"""
app/infra.py — Infrastructure helpers.

Provides reusable utilities for:
- Structlog / stdlib logging configuration
- ARQ Redis pool creation
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog
from arq import create_pool

if TYPE_CHECKING:
    from arq.connections import ArqRedis


def configure_logging(log_level: str) -> None:
    """Configure structlog for JSON-structured logging."""
    from app.config import get_settings

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            (
                structlog.dev.ConsoleRenderer()
                if get_settings().app_env == "development"
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.DEBUG),
    )


async def create_redis_pool() -> ArqRedis:
    """Create and return an ARQ Redis connection pool."""
    from app.workers.settings import get_redis_settings

    return await create_pool(get_redis_settings())
