"""
ARQ Worker: Commission Statement Generator

Triggers when both acv_received AND supplement_received
are toggled true for a job.

Pipeline:
1. Fetch job + financials.
2. Calculate gross profit and canvasser commission amount.
3. Generate Commission_Statement.pdf via PDFGenerator.
4. Register PDF in document vault.
5. Set commission_ready = 1 on the job row.
"""

import asyncio
import hashlib
from pathlib import Path

import structlog

from app.core.database import get_connection, insert_job_document
from app.services.pdf import PDFGenerator

logger = structlog.get_logger(
    "app.workers.commission_processor"
)

async def process_commission(
    ctx: dict,
    job_id: str
) -> dict:
    log = logger.bind(job_id=job_id)
    log.info("commission_processing_started")

    def _fetch():
        conn = get_connection()
        try:
            job = dict(conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,)
            ).fetchone())
            fin_row = conn.execute(
                "SELECT * FROM financials WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            return job, dict(fin_row) if fin_row else None
        finally:
            conn.close()

    job, fin = await asyncio.to_thread(_fetch)

    if not fin:
        log.error("commission_no_financials")
        return {"status": "failed",
                "reason": "no_financials_record"}

    revenue         = fin.get("revenue_cents", 0) / 100.0
    material_cost   = fin.get("material_cost_cents", 0) / 100.0
    labor_cost      = fin.get("labor_cost_cents", 0) / 100.0
    overhead_pct    = fin.get("overhead_pct", 0.25)
    commission_pct  = fin.get(
        "canvasser_commission_pct", 0.0
    )
    permits_fee     = fin.get("permits_fee_cents", 0) / 100.0

    overhead_amount = revenue * overhead_pct
    gross_profit    = (
        revenue - material_cost - labor_cost
        - overhead_amount - permits_fee
    )
    commission_amount = round(
        gross_profit * commission_pct, 2
    )

    commission_data = {
        "canvasser_name":    job.get(
            "canvasser_name", "N/A"
        ),
        "revenue_val":           revenue,
        "material_cost_val":     material_cost,
        "labor_cost_val":        labor_cost,
        "overhead_amount":   round(overhead_amount, 2),
        "gross_profit":      round(gross_profit, 2),
        "commission_pct":    commission_pct,
        "commission_amount": commission_amount,
    }

    pdf_gen  = PDFGenerator()
    pdf_path = await pdf_gen.generate_commission_statement(
        job=job, commission_data=commission_data
    )

    pdf_hash = hashlib.sha256(
        Path(pdf_path).read_bytes()
    ).hexdigest()
    insert_job_document(
        job_id=job_id,
        filename="Commission_Statement.pdf",
        file_type="COMMISSION_PDF",
        storage_path=pdf_path,
        sha256_hash=pdf_hash,
        visibility="office_only",
        category="COMMISSION_STATEMENT"
    )

    def _mark():
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE jobs
                   SET commission_ready = 1,
                       commission_pdf_path = ?,
                       commission_generated_at =
                           CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (pdf_path, job_id)
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_mark)
    log.info("commission_processing_complete",
             commission=commission_amount)
    return {
        "status":            "complete",
        "commission_amount": commission_amount,
        "pdf_path":          pdf_path
    }
