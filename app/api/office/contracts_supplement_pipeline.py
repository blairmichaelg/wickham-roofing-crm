"""
Supplement pipeline, approval, denial, rebuttal, and escalation endpoints.
Part of decomposed Office Control Center contracts module.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import structlog
from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import FileResponse, JSONResponse

from app.api.auth import (
    get_current_claims,
    verify_admin,
    verify_field,
    verify_office_role,
)
from app.core.database import (
    JobStatus,
    get_connection,
    update_job_status,
)
from app.core.pipeline import run_supplement_pipeline
from app.services.rate_limit import check_rate_limit
from app.services.security import sanitize_download_filename

logger = structlog.get_logger("app.api.office.contracts_supplement_pipeline")
router = APIRouter()


async def _run_supplement_pipeline(*args, **kwargs):
    import sys
    if "app.api.office.contracts" in sys.modules:
        mod = sys.modules["app.api.office.contracts"]
        if hasattr(mod, "run_supplement_pipeline"):
            from app.core.pipeline import run_supplement_pipeline as real_supp
            if mod.run_supplement_pipeline is not real_supp:
                res = mod.run_supplement_pipeline(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    return await res
                return res
    import unittest.mock
    if isinstance(run_supplement_pipeline, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        res = run_supplement_pipeline(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res
    return await run_supplement_pipeline(*args, **kwargs)


@router.post(
    "/jobs/{job_id}/trigger-supplement",
    response_class=JSONResponse,
    dependencies=[Depends(verify_office_role)]
)
async def trigger_supplement_route(request: Request, job_id: str, claims: dict = Depends(get_current_claims)):
    """Manually trigger or regenerate supplement pipeline for a job."""
    from app.core.pipeline import _fetch_latest_report_sync
    role = claims.get("role", "admin")

    # Locate measurement and statement of loss documents for this job
    conn = get_connection()
    try:
        job_row = conn.execute("SELECT job_type FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job_type = job_row["job_type"] if job_row else None
        cursor = conn.execute(
            """SELECT filename, storage_path, sha256_hash, id, category 
               FROM job_documents 
               WHERE job_id = ? AND category IN (
                   'MEASUREMENT_REPORT', 'EAGLEVIEW', 'EAGLEVIEW_REPORT',
                   'HOVER_REPORT', 'HOVER_PDF', 'EAGLEVIEW_PDF',
                   'STATEMENT_OF_LOSS'
               )""",
            (job_id,)
        )
        docs = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    from app.core.utils import is_retail_job
    if is_retail_job(job_type):
        from app.core.pipeline import run_retail_quote_pipeline
        redis = getattr(request.app.state, "redis_pool", None)
        if redis:
            await redis.enqueue_job("process_retail_quote", job_id=job_id)
        else:
            await run_retail_quote_pipeline(job_id=job_id)
        return {"status": "accepted", "message": "Retail quote generation triggered."}

    ev_doc = next((d for d in docs if d["category"] in (
        "MEASUREMENT_REPORT", "EAGLEVIEW", "EAGLEVIEW_REPORT",
        "HOVER_REPORT", "HOVER_PDF", "EAGLEVIEW_PDF"
    )), None)
    sol_doc = next((d for d in docs if d["category"] == "STATEMENT_OF_LOSS"), None)

    if not ev_doc or not ev_doc.get("storage_path"):
        raise HTTPException(
            status_code=400,
            detail="No measurement report (EagleView or Hover) found. Upload one first."
        )
    if not sol_doc or not sol_doc.get("storage_path"):
        raise HTTPException(
            status_code=400,
            detail="No Statement of Loss found. Upload one first."
        )

    ev_pdf_path = ev_doc["storage_path"] if ev_doc else ""
    sol_pdf_path = sol_doc["storage_path"] if sol_doc else ""
    ev_sha256 = (ev_doc.get("sha256_hash") if ev_doc else "") or ""
    ev_doc_id = (ev_doc.get("id") if ev_doc else "") or ""
    sol_sha256 = (sol_doc.get("sha256_hash") if sol_doc else "") or ""
    sol_doc_id = (sol_doc.get("id") if sol_doc else "") or ""

    has_report = await asyncio.to_thread(_fetch_latest_report_sync, job_id) is not None
    resume_flag = has_report

    try:
        if hasattr(request.app.state, "redis_pool") and request.app.state.redis_pool:
            await request.app.state.redis_pool.enqueue_job(
                "process_supplement_event",
                job_id=job_id,
                ev_pdf_path=ev_pdf_path,
                sol_pdf_path=sol_pdf_path,
                ev_sha256=ev_sha256,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_sha256,
                sol_doc_id=sol_doc_id,
                resume=resume_flag,
                generate_pdf=True,
                role=role
            )
        else:
            await _run_supplement_pipeline(
                job_id=job_id,
                ev_pdf_path=ev_pdf_path,
                sol_pdf_path=sol_pdf_path,
                ev_sha256=ev_sha256,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_sha256,
                sol_doc_id=sol_doc_id,
                resume=resume_flag,
                generate_pdf=True,
                ctx={"role": role}
            )
        return {"status": "success", "message": "Supplement processing triggered."}
    except Exception as e:
        logger.error("trigger_supplement_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/jobs/{job_id}/mark-supplement-sent",
    response_class=JSONResponse,
    dependencies=[Depends(verify_office_role)]
)
async def mark_supplement_sent_route(job_id: str):
    """Mark Supplement Sent and start carrier SLA timer."""
    from app.core.database import mark_supplement_sent
    mark_supplement_sent(job_id)
    return {"status": "ok", "job_id": job_id}


@router.get("/jobs/{job_id}/supplement/download")
async def download_supplement_pdf_route(job_id: str, role: str = Depends(verify_field), claims: dict = Depends(get_current_claims)):
    """Download the generated Supplement Request PDF."""
    from app.config import FIELD_DOCS_DIR
    pdf_path = Path(FIELD_DOCS_DIR) / job_id / "Supplement_Request.pdf"
    if not pdf_path.exists():
        conn = get_connection()
        try:
            cursor = conn.execute("SELECT storage_path FROM job_documents WHERE job_id = ? AND (category = 'SUPPLEMENT_REPORT' OR filename LIKE '%Supplement%')", (job_id,))
            row = cursor.fetchone()
            if row and Path(row["storage_path"]).exists():
                pdf_path = Path(row["storage_path"])
            else:
                raise HTTPException(status_code=404, detail="Supplement PDF not found. Please click Generate Supplement first.")
        finally:
            conn.close()

    filename = sanitize_download_filename(f"Supplement_Request_{job_id[:8]}.pdf")
    return FileResponse(path=pdf_path, media_type="application/pdf", filename=filename)


@router.post(
    "/jobs/{job_id}/approve-supplement",
    response_class=JSONResponse,
    dependencies=[Depends(verify_admin)]
)
async def approve_supplement(
    job_id: str, payload: dict = Body(default={})
):
    """
    Operator gate: transitions AWAITING_CARRIER_RESPONSE
    -> SUPPLEMENT_APPROVED.
    Triggers a WebSocket broadcast to alert Scott and Debi.
    """
    note = payload.get("note", "Approved by operator.")
    try:
        update_job_status(
            job_id, JobStatus.SUPPLEMENT_APPROVED, note
        )
        # Broadcast over existing office WebSocket
        from app.core.notifications import notifier
        await notifier.broadcast({"type": "supplement_approved",
                                  "job_id": job_id})
        return {"status": "approved", "job_id": job_id}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("approve_supplement_failed",
                     job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/jobs/{job_id}/deny-supplement",
    response_class=JSONResponse,
    dependencies=[Depends(verify_admin), Depends(check_rate_limit)]
)
async def deny_supplement(request: Request, job_id: str,
                           payload: dict = Body(...)):
    """
    Operator gate: transitions AWAITING_CARRIER_RESPONSE
    -> SUPPLEMENT_DENIED.
    Stores denial text and enqueues the rebuttal ARQ worker.
    """
    denial_text = payload.get("denial_text")
    denial_pdf_doc_id = payload.get("denial_pdf_doc_id")
    if not denial_text and not denial_pdf_doc_id:
        raise HTTPException(
            status_code=400,
            detail="Must provide denial_text or denial_pdf_doc_id."
        )
    note = f"Denied. Reason: {(denial_text or '')[:200]}"
    try:
        update_job_status(
            job_id, JobStatus.SUPPLEMENT_DENIED, note
        )
        # Enqueue rebuttal worker
        await request.app.state.redis_pool.enqueue_job(
            "process_rebuttal",
            job_id=job_id,
            denial_text=denial_text,
            denial_pdf_doc_id=denial_pdf_doc_id
        )
        return {"status": "denied_rebuttal_queued",
                "job_id": job_id}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("deny_supplement_failed",
                     job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/jobs/{job_id}/docs/rebuttal",
    response_class=FileResponse,
    dependencies=[Depends(verify_admin)]
)
async def download_rebuttal(job_id: str):
    """Download generated Rebuttal PDF."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    from app.core.database import get_job_documents
    docs = get_job_documents(job_id, file_type="REBUTTAL_PDF")
    if not docs:
        raise HTTPException(404, "No rebuttal PDF found.")
    path = docs[0]["storage_path"]
    if not Path(path).exists():
        raise HTTPException(404, "Rebuttal PDF file missing.")
    return FileResponse(
        path,
        filename=f"Rebuttal_{job_id[:8]}.pdf",
        media_type="application/pdf"
    )


@router.post("/jobs/{job_id}/escalate", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def queue_escalation(request: Request, job_id: str):
    """Queue Escalation background worker."""
    await request.app.state.redis_pool.enqueue_job(
        "process_escalation",
        job_id=job_id
    )
    return {"status": "escalation_queued"}


@router.get("/jobs/{job_id}/docs/escalation", response_class=FileResponse, dependencies=[Depends(verify_admin)])
def download_escalation(job_id: str):
    """Download Escalation Demand letter."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    from app.core.database import get_job_documents
    docs = get_job_documents(job_id, file_type="ESCALATION_PDF")
    if not docs:
        raise HTTPException(404, "No escalation letter found.")
    path = docs[0]["storage_path"]
    if not Path(path).exists():
        raise HTTPException(404, "Escalation PDF missing.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"Escalation_Demand_{job_id[:8]}.pdf"
    )
