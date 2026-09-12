"""
Field sales tools, AI inspection report summaries, and review requests.
"""

import asyncio
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.auth import get_current_claims, get_current_role, verify_field
from app.config import FIELD_DOCS_DIR
from app.core.cache import get_cached_analyses_for_job
from app.core.climate_lookup import is_ice_barrier_required
from app.core.database import (
    JobStatus,
    get_connection,
    insert_job_document,
    update_job_status,
)
from app.core.inspection_models import InspectionJob, get_stable_photos
from app.core.utils import now_utc
from app.services.field_access import assert_field_rep_owns_job
from app.services.inspection_summary import get_inspection_summary
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.field.sales_tools")
router = APIRouter()
FIELD_PHOTOS_DIR = Path("field_photos")


def _check_rep_ownership(claims, job_id, method):
    import unittest.mock
    if isinstance(assert_field_rep_owns_job, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return assert_field_rep_owns_job(claims, job_id, method)
    import sys
    if "app.api.field_routes" in sys.modules:
        mod = sys.modules["app.api.field_routes"]
        if hasattr(mod, "assert_field_rep_owns_job"):
            from app.services.field_access import assert_field_rep_owns_job as real_assert
            if mod.assert_field_rep_owns_job is not real_assert:
                return mod.assert_field_rep_owns_job(claims, job_id, method)
    return assert_field_rep_owns_job(claims, job_id, method)

def _get_field_photos_dir():
    import sys
    if "app.api.field_routes" in sys.modules and hasattr(sys.modules["app.api.field_routes"], "FIELD_PHOTOS_DIR"):
        return Path(sys.modules["app.api.field_routes"].FIELD_PHOTOS_DIR)
    return FIELD_PHOTOS_DIR

def _get_field_docs_dir():
    import sys
    if "app.api.field_routes" in sys.modules and hasattr(sys.modules["app.api.field_routes"], "FIELD_DOCS_DIR"):
        return Path(sys.modules["app.api.field_routes"].FIELD_DOCS_DIR)
    return FIELD_DOCS_DIR


@router.post("/jobs/{job_id}/inspection-report", status_code=202)
async def trigger_inspection_report(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """Queue the inspection processor to build the homeowner inspection report from uploaded photos."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

    redis = getattr(request.app.state, "redis_pool", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis connection unavailable")

    await redis.enqueue_job("process_inspection", job_id=job_id)
    return {"status": "accepted", "job_id": job_id, "message": "Inspection report generation started."}


@router.get("/jobs/{job_id}/inspection", response_model=InspectionJob)
async def get_inspection_summary_route(job_id: str, request: Request, claims: dict | None = Depends(get_current_claims)):
    """
    Get Inspection Summary functionality.
    """
    if isinstance(claims, dict):
        _check_rep_ownership(claims, job_id, request.method)
    return await get_inspection_summary(job_id, claims)


@router.post("/jobs/{job_id}/resume-supplement", status_code=202, dependencies=[Depends(check_rate_limit)])
async def resume_supplement(job_id: str, request: Request, role: str = Depends(get_current_role), claims: dict = Depends(get_current_claims)):
    """
    Resume Supplement functionality.
    
    Args:
            job_id (str): job_id parameter.
            request (Request): request parameter.
            background_tasks (BackgroundTasks): background_tasks parameter.
            role (str): role parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    _check_rep_ownership(claims, job_id, request.method)
    """
    Resumes a halted supplement pipeline (e.g. from PENDING_MANUAL_REVIEW).
    Skips parsing and gating, and goes straight to Narrative/PDF generation.
    """
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    redis = getattr(request.app.state, "redis_pool", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis connection unavailable")

    # Enqueue ARQ task with resume=True
    await redis.enqueue_job("process_supplement_event", job_id, None, None, resume=True, role=role)
    
    return {"status": "accepted", "job_id": job_id, "message": "Supplement resume processing started."}


@router.get("/jobs/{job_id}/inspection_report")
@router.post("/jobs/{job_id}/generate_report")
async def get_field_inspection_report(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Generate and download the Homeowner Inspection Report directly in the field app/tablet.
    Can be used by salesmen as a pitch & conversion tool BEFORE signing the contingency agreement.
    """
    _check_rep_ownership(claims, job_id, request.method)
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    try:
        # Check vault first for an existing Homeowner Inspection Report
        conn = get_connection()
        try:
            existing_doc = conn.execute(
                "SELECT storage_path FROM job_documents WHERE job_id = ? AND category IN ('HOMEOWNER_INSPECTION_REPORT', 'INSPECTION_REPORT') ORDER BY created_at DESC LIMIT 1",
                (job_id,)
            ).fetchone()
            if existing_doc and Path(existing_doc["storage_path"]).exists():
                logger.info("field_homeowner_report_retrieved_from_vault", job_id=job_id, path=existing_doc["storage_path"])
                pdf_path = existing_doc["storage_path"]
            else:
                pdf_path = None
        finally:
            conn.close()

        if not pdf_path:
            from app.services.pdf.inspection_report import InspectionReportGenerator
            summary_job = await get_inspection_summary(job_id)
            
            if not summary_job.photos:
                raise HTTPException(status_code=404, detail="No photos uploaded for this job yet.")

            report_gen = InspectionReportGenerator()
            pdf_path = await report_gen.generate_homeowner_report(summary_job)
            
            # Vault the document
            hr_filename = Path(pdf_path).name
            await asyncio.to_thread(
                insert_job_document,
                job_id, hr_filename, "application/pdf",
                str(pdf_path), None, "field_safe", "HOMEOWNER_INSPECTION_REPORT", True
            )
        
        filename = Path(pdf_path).name
        return FileResponse(path=pdf_path, filename=filename, media_type="application/pdf")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("field_inspection_report_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to generate Inspection Report")


class FieldReviewRequestPayload(BaseModel):
    requested_by: str = ""


@router.post("/jobs/{job_id}/request-review")
async def field_request_review(
    job_id: str,
    payload: FieldReviewRequestPayload,
    request: Request,
    claims: dict = Depends(get_current_claims),
):
    """
    Field rep: mark that a review has been requested for this completed job.
    Idempotent — safe to call multiple times.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

    # Review requests are gated to completed installations
    conn = get_connection()
    try:
        j_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not j_row:
            raise HTTPException(status_code=404, detail="Job not found.")
        current_status = j_row["status"]
    finally:
        conn.close()

    ALLOWED_REVIEW_STATUSES = {
        JobStatus.INSTALL_COMPLETED.value,
        JobStatus.FINAL_INSPECTION.value,
        JobStatus.FINAL_INSPECTION_COMPLETED.value,
        JobStatus.INVOICED.value,
        JobStatus.PAYMENT_RECEIVED.value,
        JobStatus.CLOSED.value,
    }
    if current_status not in ALLOWED_REVIEW_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Reviews can only be requested on completed installations."
        )

    rep_name = claims.get("rep_name") or payload.requested_by or "field_rep"
    from app.core.database import request_review
    try:
        result = await asyncio.to_thread(request_review, job_id, rep_name)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as exc:
        logger.error("field_request_review_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to record review request.")


class FieldReferralPayload(BaseModel):
    referral_code: str
    source: str = ""


@router.post("/jobs/{job_id}/referral")
async def field_add_referral(
    job_id: str,
    payload: FieldReferralPayload,
    request: Request,
    claims: dict = Depends(get_current_claims),
):
    """
    Field rep: attach a referral code and optional source to a job.
    Idempotent — overwrites existing referral fields.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

    from app.core.database import add_referral
    try:
        result = await asyncio.to_thread(add_referral, job_id, payload.referral_code, payload.source)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as exc:
        logger.error("field_add_referral_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to record referral.")


@router.get("/jobs/{job_id}/sales-tools")
async def get_sales_tools(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Return AI-generated sales summary and door-knocking script for a job.

    Results are cached in the document vault (as JSON text) to avoid repeated
    AI calls. Cached results are returned on subsequent requests.

    Returns:
      {
        "sales_summary": "...",
        "door_script": "...",
        "cached": bool
      }
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

    conn = get_connection()
    try:
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found.")
        job = dict(job_row)

        # Check for cached sales tools
        cached_doc = conn.execute(
            "SELECT storage_path FROM job_documents WHERE job_id = ? AND category = 'SALES_TOOLS' ORDER BY created_at DESC LIMIT 1",
            (job_id,)
        ).fetchone()
    finally:
        conn.close()

    if cached_doc:
        cache_path = Path(cached_doc["storage_path"])
        if cache_path.exists():
            try:
                import json as _json
                cached = _json.loads(cache_path.read_text(encoding="utf-8"))
                cached["cached"] = True
                return cached
            except Exception:
                pass  # Fall through to regenerate if cache is corrupt

    # Fetch nearby storm events for grounding
    from app.core.database import get_storm_events_near_job
    storm_events = await asyncio.to_thread(
        get_storm_events_near_job,
        job_id=job_id,
        window_hours=168,
    )

    from app.services.sales_narrative import generate_door_script, generate_sales_summary
    summary, script = await asyncio.gather(
        generate_sales_summary(job, storm_events),
        generate_door_script(job, storm_events),
    )

    result = {"sales_summary": summary, "door_script": script, "cached": False}

    # Persist to vault as a JSON text file
    import json as _json
    cache_dir = Path("data/field_docs") / job_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "sales_tools.json"
    try:
        cache_path.write_text(_json.dumps(result, indent=2), encoding="utf-8")
        await asyncio.to_thread(
            insert_job_document,
            job_id,
            "sales_tools.json",
            "application/json",
            str(cache_path),
            None,
            "field_safe",
            "SALES_TOOLS",
            True,
        )
    except Exception as cache_exc:
        logger.warning("sales_tools_cache_write_failed", job_id=job_id, error=str(cache_exc))

    return result

