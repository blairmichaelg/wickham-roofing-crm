"""
ARQ Worker: Retail Quote Generator
"""

import structlog

from app.core.pipeline import run_retail_quote_pipeline

logger = structlog.get_logger("app.workers.retail_quote_processor")

async def process_retail_quote(ctx: dict, job_id: str) -> dict:
    return await run_retail_quote_pipeline(job_id)
