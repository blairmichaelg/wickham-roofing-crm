"""
Field lead intake, job status inspection, claim info and flags.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.api.auth import get_current_claims, get_current_role, verify_field
from app.config import FIELD_DOCS_DIR
from app.core.climate_lookup import is_ice_barrier_required
from app.core.constants import ErrorCode
from app.core.database import (
    JobStatus,
    get_connection,
    update_job_status,
)
from app.core.notifications import notifier
from app.core.utils import normalize_zip, now_utc, now_utc_iso
from app.services.field_access import assert_field_rep_owns_job

logger = structlog.get_logger("app.api.field.leads")
router = APIRouter()
FIELD_PHOTOS_DIR = Path("field_photos")


def _sync_fetch_job_contingency(job_id: str):
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(job_row)
    finally:
        conn.close()


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


class LeadIntakePayload(BaseModel):
    """LeadIntakePayload definition."""
    homeowner_name: str
    address_line1: str
    city: str
    state: str
    postal_code: str
    phone: str
    email: str | None = None
    claim_number: str | None = None
    insurer_name: str | None = None
    job_type: str = Field(default="INSURANCE")
    loss_date: str | None = None
    canvasser_name: str | None = None


class FieldClaimInfoPayload(BaseModel):
    claim_number: str | None = None
    insurer_name: str | None = None
    loss_date: str | None = None
    policy_number: str | None = None
    adjuster_name: str | None = None
    adjuster_phone: str | None = None
    adjuster_email: str | None = None


class FlagResolutionPayload(BaseModel):
    """FlagResolutionPayload definition."""
    quantity_delta: float = Field(..., description="The corrected, manually determined quantity")
    resolution_note: str = Field(..., description="Audit note explaining the manual override")


def _sync_create_new_job(job_id: str, inv_id: str, payload: LeadIntakePayload, ice_barrier: bool | None, canvasser_name: str, canvasser_rep_id: str | None = None):
    conn = get_connection()
    try:
        initial_history = [{
            "status": "LEAD_CAPTURED",
            "timestamp": now_utc_iso(),
            "note": "Initial canvasser intake via Wickham Roofing CRM"
        }]
        
        norm_zip = normalize_zip(payload.postal_code)
        conn.execute('''
            INSERT INTO jobs (
                id, invoice_id, homeowner_name, address_line1, city, state, postal_code, 
                phone, email, claim_number, insurer_name, status, status_history, job_type,
                ice_barrier_required, jurisdiction_code_version, canvasser_name, canvasser_rep_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            job_id, inv_id, payload.homeowner_name, payload.address_line1, payload.city,
            payload.state, norm_zip, payload.phone, payload.email,
            payload.claim_number, payload.insurer_name, "LEAD_CAPTURED",
            json.dumps(initial_history), payload.job_type,
            ice_barrier, "2021_IRC", canvasser_name, canvasser_rep_id
        ))
        
        if payload.loss_date:
            sv_id = str(uuid.uuid4())
            conn.execute('''
                INSERT INTO storm_verifications (id, job_id, loss_date, event_type, begin_lat, begin_lon, match_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (sv_id, job_id, payload.loss_date, 'Unknown', 0.0, 0.0, 'Pending'))
            
        conn.commit()
    finally:
        conn.close()


@router.post("/jobs")
async def create_new_job(
    payload: LeadIntakePayload,
    request: Request,
    role: str = Depends(verify_field),
    claims: dict = Depends(get_current_claims),
):
    """
    Intake hook for new leads. Replaces external CRM lead creation.
    Generates UUID, creates directories, and initializes local SQLite record.
    """
    job_id = str(uuid.uuid4())
    
    # Determine climate requirements
    ice_barrier = is_ice_barrier_required(payload.state)
    
    # Generate invoice ID
    from app.core.database import generate_invoice_id
    inv_id = generate_invoice_id()

    # Resolve canvasser identity from JWT claims first, then payload fallback
    canvasser_name = (
        claims.get("rep_name")           # from JWT (field rep identity)
        or (payload.canvasser_name or "").strip()  # manual override in payload
        or "Unassigned"                   # last resort
    )
    rep_id = claims.get("rep_id")
    
    # Insert into database using background thread
    try:
        await asyncio.to_thread(_sync_create_new_job, job_id, inv_id, payload, ice_barrier, canvasser_name, rep_id)
        
        await notifier.broadcast({
            "type": "new_lead",
            "job": {
                "id": job_id,
                "homeowner_name": payload.homeowner_name,
                "address_line1": payload.address_line1,
                "city": payload.city,
                "state": payload.state,
                "status": "LEAD_CAPTURED",
                "ice_barrier_required": ice_barrier
            }
        })
    except Exception as e:
        logger.error("lead_intake_db_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Database insertion failed")

    # Create local directories
    (_get_field_photos_dir() / job_id).mkdir(parents=True, exist_ok=True)
    (_get_field_docs_dir() / job_id).mkdir(parents=True, exist_ok=True)
    
    # Fork retail jobs to retail quote worker
    from app.core.utils import is_retail_job
    if is_retail_job(payload.job_type):
        if hasattr(request.app.state, "redis_pool") and request.app.state.redis_pool:
            await request.app.state.redis_pool.enqueue_job(
                "process_retail_quote", job_id=job_id
            )

    logger.info("new_lead_captured", job_id=job_id, invoice_id=inv_id, homeowner=payload.homeowner_name)
    return {"status": "success", "job_id": job_id}


@router.get("/jobs")
async def list_my_jobs(claims: dict = Depends(get_current_claims)):
    """List jobs for the current rep.

    Matches canvasser_name OR canvasser_rep_id so admin/core-team leads
    created via the dropdown always appear in their recent jobs list.
    """
    rep_name = claims.get("rep_name")
    rep_id = claims.get("rep_id")
    if not rep_name and not rep_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT id, homeowner_name, address_line1, city, state, postal_code,
                   phone, email, claim_number, insurer_name, job_type, loss_date,
                   created_at, status
            FROM jobs
            WHERE (canvasser_name = ? OR (canvasser_rep_id IS NOT NULL AND canvasser_rep_id = ?))
            ORDER BY created_at DESC LIMIT 50
            """,
            (rep_name, rep_id)
        )
        jobs = [dict(r) for r in cursor.fetchall()]
        from app.core.database import add_storm_flags_to_jobs
        return add_storm_flags_to_jobs(jobs)
    finally:
        conn.close()


@router.get("/jobs/{job_id}")
async def get_field_job_details(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """Retrieve full details of a specific job for field resumption."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    _check_rep_ownership(claims, job_id, request.method)
    job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")
    from app.core.database import add_storm_flags_to_jobs
    flagged = add_storm_flags_to_jobs([job_dict])
    return flagged[0] if flagged else job_dict


def _sync_resolve_flag(job_id: str, flag_id: str, payload: FlagResolutionPayload):
    conn = get_connection()
    try:
        # Verify the flag exists and belongs to the job
        cursor = conn.execute("SELECT id FROM supplement_flags WHERE id = ? AND job_id = ?", (flag_id, job_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Flag not found or does not belong to this job.")
        
        # Update the flag
        audit_note = f"RESOLVED: {payload.resolution_note}"
        conn.execute('''
            UPDATE supplement_flags
            SET quantity_delta = ?, notes = ?
            WHERE id = ?
        ''', (payload.quantity_delta, audit_note, flag_id))
        conn.commit()
    finally:
        conn.close()


@router.patch("/jobs/{job_id}/flags/{flag_id}", status_code=200)
async def resolve_flag(job_id: str, flag_id: str, payload: FlagResolutionPayload, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Resolve Flag functionality.
    
    Args:
            job_id (str): job_id parameter.
            flag_id (str): flag_id parameter.
            payload (FlagResolutionPayload): payload parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    _check_rep_ownership(claims, job_id, request.method)
    """
    Resolves a flag that was marked for manual review.
    Updates the quantity and adds a resolution note.
    """
    try:
        uuid.UUID(job_id)
        uuid.UUID(flag_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id or flag_id format. Must be a valid UUID.")

    await asyncio.to_thread(_sync_resolve_flag, job_id, flag_id, payload)
        
    return {"status": "success", "flag_id": flag_id, "message": "Flag resolved successfully."}


@router.patch("/jobs/{job_id}/claim-info", status_code=200)
async def update_field_claim_info(job_id: str, payload: FieldClaimInfoPayload, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Allow field reps/salesmen to update claim metadata (insurer, claim #, loss date, policy #, etc.)
    for jobs they own.
    """
    _check_rep_ownership(claims, job_id, request.method)
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    from app.core.database import update_job_claim_info
    try:
        res = await asyncio.to_thread(
            update_job_claim_info,
            job_id=job_id,
            claim_number=payload.claim_number,
            insurer_name=payload.insurer_name,
            loss_date=payload.loss_date,
            policy_number=payload.policy_number,
            adjuster_name=payload.adjuster_name,
            adjuster_phone=payload.adjuster_phone,
            adjuster_email=payload.adjuster_email,
        )
        # Auto-advance to CLAIM_FILED if claim info provided for early stage lead
        if payload.claim_number or payload.insurer_name:
            from app.core.database import JobStatus, _fetch_job_sync, update_job_status
            job = await asyncio.to_thread(_fetch_job_sync, job_id)
            if job and job.get("status") in (JobStatus.LEAD_CAPTURED, JobStatus.CONTINGENCY_SIGNED):
                try:
                    await asyncio.to_thread(update_job_status, job_id, JobStatus.CLAIM_FILED, "Insurance claim info filed by field rep.")
                except Exception:
                    pass

        return res
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error("field_claim_info_update_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update claim metadata")


@router.get("/pipeline/summary")
async def get_field_pipeline_summary(claims: dict = Depends(get_current_claims)):
    """
    Get pipeline metrics for the logged-in field representative.
    """
    rep_name = claims.get("rep_name")
    rep_id = claims.get("rep_id")
    if not rep_name and not rep_id:
        return {
            "status": "success",
            "stage_counts": {
                "LEAD_CAPTURED": 0,
                "CONTINGENCY_SIGNED": 0,
                "CLAIM_FILED": 0,
                "RETAIL_CONTRACT_SIGNED": 0,
                "SCOPE_APPROVED": 0,
                "INSTALL_SCHEDULED": 0,
                "INSTALL_COMPLETED": 0,
                "PAYMENT_RECEIVED": 0,
                "CLOSED": 0,
            },
            "total_active": 0,
            "avg_speed_to_lead_hours": None
        }

    conn = get_connection()
    try:
        SALES_STAGES = [
            "LEAD_CAPTURED",
            "CONTINGENCY_SIGNED",
            "CLAIM_FILED",
            "RETAIL_CONTRACT_SIGNED",
            "SCOPE_APPROVED",
            "INSTALL_SCHEDULED",
            "INSTALL_COMPLETED",
            "PAYMENT_RECEIVED",
            "CLOSED",
        ]
        
        cursor = conn.execute(
            """
            SELECT status, COUNT(*) AS cnt 
            FROM jobs 
            WHERE (canvasser_name = ? OR (canvasser_rep_id IS NOT NULL AND canvasser_rep_id = ?))
            GROUP BY status
            """,
            (rep_name, rep_id)
        )
        stage_counts = {s: 0 for s in SALES_STAGES}
        total_active = 0
        for r in cursor.fetchall():
            status_val = r["status"]
            if status_val in stage_counts:
                stage_counts[status_val] = r["cnt"]
            if status_val not in ("CLOSED", "CLAIM_DENIED", "SUPPLEMENT_DENIED"):
                total_active += r["cnt"]

        # Calculate average speed to lead duration
        cursor = conn.execute(
            """
            SELECT status_history 
            FROM jobs 
            WHERE (canvasser_name = ? OR (canvasser_rep_id IS NOT NULL AND canvasser_rep_id = ?))
              AND status_history IS NOT NULL
            """,
            (rep_name, rep_id)
        )
        durations = []
        from app.core.utils import calculate_speed_to_lead
        for r in cursor.fetchall():
            h_val = calculate_speed_to_lead(r["status_history"])
            if h_val is not None:
                durations.append(h_val)

        avg_speed = round(sum(durations) / len(durations), 2) if durations else None

        return {
            "status": "success",
            "stage_counts": stage_counts,
            "total_active": total_active,
            "avg_speed_to_lead_hours": avg_speed,
        }
    finally:
        conn.close()

