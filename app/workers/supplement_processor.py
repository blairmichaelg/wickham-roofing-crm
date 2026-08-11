"""
ARQ Worker: Supplement Pipeline orchestrator.
"""

import traceback

import structlog
from arq.worker import Retry

from app.core.database import JobStatus, get_connection
from app.core.pipeline import run_supplement_pipeline

logger = structlog.get_logger("app.workers.supplement_processor")

VALID_WORKER_ROLES = {"admin", "field", "office", "accounting", "operations"}

async def process_supplement_event(
    ctx: dict,
    job_id: str,
    ev_pdf_path: str = "",
    sol_pdf_path: str = "",
    ev_sha256: str = "",
    ev_doc_id: str = "",
    sol_sha256: str = "",
    sol_doc_id: str = "",
    resume: bool = False,
    generate_pdf: bool = True,
    role: str | None = None,
) -> dict:
    job_try = ctx.get('job_try', 1)
    
    # Sanitize role — default to admin/office if not specified
    if role not in VALID_WORKER_ROLES:
        logger.warning("invalid_role_in_payload", job_id=job_id, role=role)
        role = "admin"

    ctx["role"] = role
    ALLOWED_SUPPLEMENT_ROLES = {"admin", "operations", "accounting", "office"}
    if ctx.get("role") not in ALLOWED_SUPPLEMENT_ROLES:
        logger.warning("role_not_allowed_for_supplement", role=role)
        return {"status": "forbidden", "reason": "role_not_allowed_for_supplement"}

    try:
        return await run_supplement_pipeline(
            job_id, ev_pdf_path, sol_pdf_path, ev_sha256, ev_doc_id, sol_sha256, sol_doc_id, resume, generate_pdf, ctx
        )
    except Exception as e:
        error_msg = str(e)
        logger.error("supplement_processing_failed", job_id=job_id, error=error_msg, try_num=job_try)
        
        # Determine if it's a transient network/API error
        is_transient = "timeout" in error_msg.lower() or "429" in error_msg or "connection" in error_msg.lower()
        
        if is_transient and job_try < 3:
            logger.info("retrying_supplement_processing", defer=job_try * 10)
            raise Retry(defer=job_try * 10)
        
        # Permanent Failure Flow
        error_trace = traceback.format_exc()
        
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Update Job Status to PENDING_OPERATOR_REVIEW instead of PENDING_MANUAL_REVIEW as it already exists in the enum
            from app.core.database import _update_job_status_internal
            _update_job_status_internal(conn, job_id, JobStatus.PENDING_OPERATOR_REVIEW, "Supplement drafting failed")
            
            # Insert trace into job_tasks for triage board
            conn.execute(
                "INSERT INTO job_tasks (job_id, task_type, phase, last_error) VALUES (?, ?, ?, ?)",
                (job_id, "SUPPLEMENT_DRAFTING", "GENERATION", error_trace)
            )
            conn.execute("COMMIT")
        except Exception as db_e:
            conn.execute("ROLLBACK")
            logger.error("failed_to_log_supplement_failure", error=str(db_e))
        finally:
            conn.close()
            
        raise # Let ARQ mark it failed
