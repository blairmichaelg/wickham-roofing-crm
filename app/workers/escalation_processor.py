"""
ARQ Worker: Escalation Demand Letter Generator
V2 — Full Financial Context + Appraisal Terminal Gate

Pipeline:
1. Fetch job record + financials + latest supplement report.
2. Check escalation_sent_at:
   - If NULL: First Escalation.
     → Inject financials into prompt.
     → Generate "Second Request" letter via AIService.
     → Generate PDF via PDFGenerator.generate_escalation_letter.
     → Set escalation_sent_at = NOW.
     → Reset supplement_sent_at = NOW (restarts the 14-day SLA clock).
   - If NOT NULL: Second Escalation (carrier ignored the demand letter).
     → Transition job to APPRAISAL_INVOKED terminal state.
     → Do NOT generate a PDF — manual legal process takes over.
     → Return {"status": "appraisal_invoked"}.
"""

import asyncio
import hashlib
from datetime import datetime as _dt
from pathlib import Path

import structlog

from app.core.database import (
    JobStatus,
    get_connection,
    insert_job_document,
    update_job_status,
)
from app.services.ai_service import get_ai_client
from app.services.pdf import PDFGenerator

logger = structlog.get_logger("app.workers.escalation_processor")

ESCALATION_SYSTEM_PROMPT = """
You are a licensed public adjuster writing a formal Second Request \
and Notice of Intent to Invoke Appraisal.

Your letter MUST:
1. State the original supplement submission date and the exact number \
   of days elapsed with no carrier response.
2. Cite EVERY outstanding disputed line item and its exact dollar amount \
   as provided in the context below.
3. State the TOTAL outstanding disputed amount in bold.
4. Formally invoke the policyholder's statutory right to appraisal under \
   the standard insurance policy appraisal clause.
5. Set a hard 10-business-day deadline for the carrier to respond in \
   writing before appraisal proceedings are formally initiated.
6. Be firm, professional, and legally precise. No emotional language. \
   No hedging.
"""


async def process_escalation(ctx: dict, job_id: str) -> dict:
    log = logger.bind(job_id=job_id)
    log.info("escalation_processing_started")

    def _fetch_all():
        conn = get_connection()
        try:
            job_row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not job_row:
                return None, None, None
            job = dict(job_row)

            fin_row = conn.execute(
                "SELECT * FROM financials WHERE job_id = ?", (job_id,)
            ).fetchone()

            rpt_row = conn.execute(
                """SELECT report_json
                   FROM supplement_reports
                   WHERE job_id = ?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (job_id,),
            ).fetchone()

            return (
                job,
                dict(fin_row) if fin_row else None,
                rpt_row["report_json"] if rpt_row else None,
            )
        finally:
            conn.close()

    job, fin, report_json = await asyncio.to_thread(_fetch_all)

    if not job:
        log.error("escalation_job_not_found")
        return {"status": "failed", "reason": "job_not_found"}

    # ── GATE: Second offense → invoke appraisal terminal state ──────────
    if job.get("escalation_sent_at"):
        log.warning("escalation_second_offense_detected")

        def _invoke_appraisal():
            update_job_status(
                job_id,
                JobStatus.APPRAISAL_INVOKED,
                "Carrier failed to respond to escalation demand letter "
                "within SLA. Appraisal invoked. Manual handling required.",
            )

        await asyncio.to_thread(_invoke_appraisal)
        log.error("appraisal_invoked", job_id=job_id)
        return {"status": "appraisal_invoked", "job_id": job_id}

    # ── Calculate days elapsed since supplement was sent ─────────────────
    days_elapsed = 0
    sent_at_str = job.get("supplement_sent_at", "")
    if sent_at_str:
        try:
            sent = _dt.fromisoformat(str(sent_at_str).replace("Z", ""))
            days_elapsed = (_dt.now(__import__('datetime').timezone.utc).replace(tzinfo=None) - sent).days
        except Exception:
            pass

    # ── Build financial context for AI prompt ─────────────────────────────
    fin_context = "Financial data not available."
    if fin:
        revenue = (fin.get("revenue_cents", 0) or 0) / 100.0
        carrier = (fin.get("carrier_rcv_cents", 0) or 0) / 100.0
        delta = revenue - carrier
        fin_context = (
            f"  Contract Revenue: ${revenue:,.2f}\n"
            f"  Carrier Initial RCV: ${carrier:,.2f}\n"
            f"  Outstanding Disputed Amount: ${delta:,.2f}\n"
        )

    user_prompt = f"""
HOMEOWNER: {job.get('homeowner_name')}
ADDRESS:   {job.get('address_line1')}, {job.get('city')}, {job.get('state')}
CLAIM #:   {job.get('claim_number', 'N/A')}
INSURER:   {job.get('insurer_name', 'N/A')}
INVOICE:   {job.get('invoice_id', 'N/A')}

SUPPLEMENT SUBMITTED:
    {job.get('supplement_sent_at', 'N/A')}
DAYS WITHOUT CARRIER RESPONSE: {days_elapsed}
SLA THRESHOLD: {job.get('carrier_sla_days', 14)} days

FINANCIAL CONTEXT:
{fin_context}

SUPPLEMENT DISCREPANCY REPORT (use for line items):
{(report_json or 'Not available')[:4000]}

Write the complete formal Second Request / Notice of Intent to Appraise
letter body. Include all specific disputed dollar amounts from the
financial context and supplement report.
"""

    ai = get_ai_client()
    try:
        narrative = await ai.generate_text(
            system_prompt=ESCALATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            job_id=job_id,
            operation_type="escalation_generation",
        )
    except Exception as e:
        log.error("escalation_ai_failed", error=str(e))
        return {"status": "failed", "reason": str(e)}

    # ── Generate PDF ───────────────────────────────────────────────────────
    pdf_gen = PDFGenerator()
    pdf_path = await pdf_gen.generate_escalation_letter(
        job=job,
        days_elapsed=days_elapsed,
        narrative=narrative,
    )

    pdf_hash = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    insert_job_document(
        job_id=job_id,
        filename="Escalation_Demand_Letter.pdf",
        file_type="ESCALATION_PDF",
        storage_path=pdf_path,
        sha256_hash=pdf_hash,
        visibility="office_only",
        category="ESCALATION_REPORT"
    )

    # ── Mark escalation sent AND reset supplement_sent_at (restart SLA) ──
    def _mark():
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE jobs
                SET escalation_sent_at = CURRENT_TIMESTAMP,
                    supplement_sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_mark)

    log.info(
        "escalation_complete",
        days_elapsed=days_elapsed,
        pdf_path=pdf_path,
    )
    return {
        "status": "complete",
        "pdf_path": pdf_path,
        "days_elapsed": days_elapsed,
    }
