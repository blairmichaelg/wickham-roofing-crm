"""
ARQ Worker: Rebuttal Letter Generator
"""

import structlog

from app.core.pipeline import run_rebuttal_pipeline

logger = structlog.get_logger("app.workers.rebuttal_processor")

async def process_rebuttal(
    ctx: dict,
    job_id: str,
    denial_text: str | None = None,
    denial_pdf_doc_id: str | None = None
) -> dict:
    return await run_rebuttal_pipeline(job_id, denial_text, denial_pdf_doc_id)
