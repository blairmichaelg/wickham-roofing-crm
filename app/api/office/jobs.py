"""
Jobs management, triage, and claim/shingle info for Office Control Center.
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.auth import (
    get_current_claims,
    get_current_role,
    verify_admin,
    verify_field,
    verify_office_role,
)
from app.core.backup import backup_database
from app.core.database import (
    JobStatus,
    _fetch_job_sync,
    add_referral,
    get_connection,
    get_sales_pipeline_summary,
    request_review,
    update_job_status,
)
from app.core.templates import templates
from app.core.utils import now_utc
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.office.jobs")
router = APIRouter()

class ShingleInfoPayload(BaseModel):
    """Payload for PATCH /jobs/{job_id}/shingle-info."""
    shingle_color: str | None = None
    shingle_type: str | None = None


class JobClaimInfoPayload(BaseModel):
    claim_number: str | None = None
    insurer_name: str | None = None
    loss_date: str | None = None  # ISO date string
    policy_number: str | None = None
    adjuster_name: str | None = None
    adjuster_phone: str | None = None
    adjuster_email: str | None = None
    ice_barrier_required: bool | None = None


@router.get("/jobs", dependencies=[Depends(verify_admin)])
def get_all_jobs() -> list[dict[str, str | float | int | list | None]]:
    """
    Retrieve all jobs from the local CRM ordered by creation date.
    
    Returns:
        List[Dict[str, Union[str, float, int, list, None]]]: A list of job records.
    """
    conn = get_connection()
    try:
        cursor = conn.execute('''
            SELECT id, homeowner_name, address_line1, city, state, postal_code, 
                   phone, email, claim_number, insurer_name, status, status_history, created_at
            FROM jobs
            ORDER BY created_at DESC
        ''')
        rows = cursor.fetchall()
        
        jobs = []
        for r in rows:
            job_dict = dict(r)
            job_dict["status_history"] = json.loads(job_dict["status_history"]) if job_dict["status_history"] else []
            jobs.append(job_dict)
            
        return jobs
    except Exception as e:
        logger.error("failed_to_fetch_jobs", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch jobs")
    finally:
        conn.close()


@router.get("/jobs/sanity-check", dependencies=[Depends(verify_admin)])
async def get_jobs_sanity_check(limit: int = 50):
    """
    Lightweight read-only operator/admin sanity check view.
    Surfaces recent jobs, their status, last_payment_received_at, storm flags,
    and flags known financial or workflow anomalies.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, homeowner_name, postal_code, status, last_payment_received_at, created_at
            FROM jobs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        raw_jobs = [dict(r) for r in cur.fetchall()]
        from app.core.database import add_storm_flags_to_jobs
        flagged_jobs = add_storm_flags_to_jobs(raw_jobs)

        results = []
        anomaly_count = 0
        for job in flagged_jobs:
            status = job.get("status")
            last_pmt = job.get("last_payment_received_at")
            anomalies = []

            # Check 1: Payment received status but missing timestamp
            if status in ("PAYMENT_RECEIVED", "ACV_PAYMENT_RECEIVED", "DEPRECIATION_PAYMENT_RECEIVED", "RETAIL_PAYMENT_RECEIVED") and not last_pmt:
                anomalies.append(f"Status is '{status}' but last_payment_received_at is missing.")

            # Check 2: Payment timestamp exists but job is still pre-invoiced or unbilled
            pre_invoice_statuses = {
                "LEAD_CAPTURED", "CONTINGENCY_SIGNED", "CLAIM_FILED",
                "INSPECTION_COMPLETED", "SCOPE_APPROVED", "SUPPLEMENT_GENERATED",
                "SUPPLEMENT_APPROVED", "MATERIAL_ORDERED", "MATERIALS_ON_SITE",
                "INSTALL_SCHEDULED", "INSTALL_COMPLETED", "FINAL_INSPECTION_COMPLETED"
            }
            if last_pmt and status in pre_invoice_statuses:
                anomalies.append(f"last_payment_received_at is set ({last_pmt}) but status is '{status}' (pre-invoiced).")

            if last_pmt and status == "INVOICED":
                anomalies.append(f"Payment timestamp set ({last_pmt}) but status is still 'INVOICED'.")

            if anomalies:
                anomaly_count += 1

            results.append({
                "job_id": job["id"],
                "homeowner_name": job.get("homeowner_name"),
                "postal_code": job.get("postal_code"),
                "status": status,
                "last_payment_received_at": last_pmt,
                "has_recent_hail": job.get("has_recent_hail", False),
                "has_recent_wind": job.get("has_recent_wind", False),
                "recent_hail_max_inches": job.get("recent_hail_max_inches", 0.0),
                "recent_wind_max_mph": job.get("recent_wind_max_mph", 0.0),
                "storm_window_hours": job.get("storm_window_hours", 72),
                "anomalies": anomalies,
                "has_anomaly": len(anomalies) > 0,
            })

        return {
            "total_inspected": len(results),
            "anomaly_count": anomaly_count,
            "jobs": results
        }
    finally:
        conn.close()


@router.get("/jobs/{job_id}", dependencies=[Depends(verify_admin)])
def get_job_details(job_id: str) -> dict[str, dict[str, str | float | int | list | None] | list[dict[str, str | float | int | None]] | None]:
    """
    Retrieve unified job details across all production tables.
    
    Args:
        job_id (str): The unique identifier of the job.
        
    Returns:
        Dict[str, Union[Dict, List, None]]: Aggregated job data including financials, schedule, and docs.
    """
    conn = get_connection()
    try:
        # Get Job Metadata
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found.")
        job_dict = dict(job_row)
        job_dict["status_history"] = json.loads(job_dict["status_history"]) if job_dict["status_history"] else []
        
        # Get Financials
        fin_dict = get_financials(job_id)
        
        # Get Schedule
        cursor = conn.execute("SELECT * FROM schedule WHERE job_id = ?", (job_id,))
        sched_row = cursor.fetchone()
        
        # Get Material Order (Most recent)
        cursor = conn.execute("SELECT * FROM material_orders WHERE job_id = ? ORDER BY delivery_date DESC LIMIT 1", (job_id,))
        mat_row = cursor.fetchone()
        
        if fin_dict:
            # Dynamically compute exact margins
            margins = compute_job_profitability(
                revenue_cents=fin_dict["revenue_cents"],
                materials_cents=fin_dict["material_cost_cents"],
                labor_cents=fin_dict["labor_cost_cents"],
                overhead_pct=fin_dict["overhead_pct"],
                commission_pct=fin_dict["canvasser_commission_pct"],
                commission_pct_override=job_dict.get("commission_pct_override")
            )
            
            # Convert back to dollars for the UI payload
            margins_dollars = {
                "direct_costs": margins["direct_costs_cents"] / 100.0,
                "gross_profit": margins["gross_profit_cents"] / 100.0,
                "gross_margin": margins["gross_margin"],
                "overhead_cost": margins["overhead_cost_cents"] / 100.0,
                "net_profit": margins["net_profit_cents"] / 100.0,
                "canvasser_commission": margins["canvasser_commission_cents"] / 100.0,
                "effective_commission_pct": margins["effective_commission_pct"]
            }
            fin_dict["computed_margins"] = margins_dollars

        # Get Documents
        cursor = conn.execute("SELECT * FROM job_documents WHERE job_id = ? ORDER BY created_at DESC", (job_id,))
        doc_rows = cursor.fetchall()
        docs = [dict(r) for r in doc_rows]

        return {
            "job": job_dict,
            "financials": fin_dict,
            "schedule": dict(sched_row) if sched_row else None,
            "material_order": dict(mat_row) if mat_row else None,
            "documents": docs
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("failed_to_fetch_job_details", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch job details")
    finally:
        conn.close()


@router.patch("/jobs/{job_id}/claim-info", dependencies=[Depends(verify_field)])
async def update_claim_info_route(job_id: str, payload: JobClaimInfoPayload, bg_tasks: BackgroundTasks):
    """
    Update insurance claim metadata (insurer, claim #, loss date, policy #, adjuster info)
    for a job at any point in time. Accessible to both core team and field reps.
    """
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
            ice_barrier_required=payload.ice_barrier_required,
        )
        # Auto-advance to CLAIM_FILED if claim info provided for early stage lead
        if payload.claim_number or payload.insurer_name:
            job = await asyncio.to_thread(_fetch_job_sync, job_id)
            if job and job.get("status") in (JobStatus.LEAD_CAPTURED, JobStatus.CONTINGENCY_SIGNED):
                try:
                    await asyncio.to_thread(update_job_status, job_id, JobStatus.CLAIM_FILED, "Insurance claim info filed by user.")
                except Exception:
                    pass

        bg_tasks.add_task(backup_database)
        return res
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error("claim_info_update_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update claim metadata")


@router.patch("/jobs/{job_id}/shingle-info", dependencies=[Depends(verify_field)])
async def update_shingle_info_route(job_id: str, payload: ShingleInfoPayload, bg_tasks: BackgroundTasks):
    """
    Update shingle color and type on a job record.
    Accessible to both core team (Admin, Operations, Accounting) and field reps.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    from app.core.database import update_shingle_info
    try:
        res = await asyncio.to_thread(
            update_shingle_info,
            job_id=job_id,
            shingle_color=payload.shingle_color,
            shingle_type=payload.shingle_type,
        )
        bg_tasks.add_task(backup_database)
        return res
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error("shingle_info_update_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update shingle info")


def _sync_update_job_claim_info(job_id: str, payload: JobClaimInfoPayload):
    conn = get_connection()
    try:
        updates = {}
        if payload.claim_number is not None:
            updates["claim_number"] = payload.claim_number
        if payload.insurer_name is not None:
            updates["insurer_name"] = payload.insurer_name
            
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [job_id]
            conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
            
        if payload.loss_date is not None:
            cursor = conn.execute("SELECT id FROM storm_verifications WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if row:
                conn.execute("UPDATE storm_verifications SET loss_date = ? WHERE job_id = ?", (payload.loss_date, job_id))
            else:
                sv_id = str(uuid.uuid4())
                conn.execute('''
                    INSERT INTO storm_verifications (id, job_id, loss_date, event_type, begin_lat, begin_lon, match_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (sv_id, job_id, payload.loss_date, 'Unknown', 0.0, 0.0, 'Pending'))
        conn.commit()
    finally:
        conn.close()


@router.get("/admin/triage", response_class=HTMLResponse, dependencies=[Depends(verify_admin)])
async def admin_triage_view(request: Request):
    """
    Admin Triage View functionality.
    
    Args:
            request (Request): request parameter.
    
    Returns:
        Any: The resulting output.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT j.id, j.homeowner_name, j.address_line1,
                   j.status, j.created_at,
                   j.pipeline_error_message,
                   (
                       SELECT jt.last_error
                       FROM job_tasks jt
                       WHERE jt.job_id = j.id
                         AND jt.last_error IS NOT NULL
                       ORDER BY CASE jt.task_type WHEN 'SUPPLEMENT_DRAFTING' THEN 0 WHEN 'INSPECTION_VISION' THEN 1 ELSE 2 END
                       LIMIT 1
                   ) AS last_error,
                   j.ev_total_area_sf, j.ev_predominant_pitch,
                   j.ev_ridge_lf, j.ev_hip_lf,
                   j.ev_valley_lf, j.ev_eaves_lf, j.ev_rakes_lf
            FROM jobs j
            WHERE j.status IN ('PENDING_OPERATOR_REVIEW', 'PIPELINE_FAILED', 'INSPECTION_FAILED')
            ORDER BY j.created_at ASC
        """)
        stuck_jobs = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "admin_triage.html",
        {"request": request, "stuck_jobs": stuck_jobs}
    )


class ManualMeasurementPayload(BaseModel):
    total_area_sf: float = Field(..., ge=0, description="Total roof surface area in sq ft")
    predominant_pitch: str = Field(..., description="Predominant pitch e.g. 6/12")
    ridge_lf: float = Field(default=0.0, ge=0)
    hip_lf: float = Field(default=0.0, ge=0)
    valley_lf: float = Field(default=0.0, ge=0)
    eaves_lf: float = Field(default=0.0, ge=0)
    rake_lf: float = Field(default=0.0, ge=0)
    drip_edge_lf: float | None = Field(default=None, ge=0)
    flashing_lf: float | None = Field(default=None, ge=0)
    step_flashing_lf: float | None = Field(default=None, ge=0)
    flashing_wall_lf: float | None = Field(default=None, ge=0)
    total_facets: int | None = Field(default=None, ge=0)
    pipe_boot_count: int | None = Field(default=None, ge=0)
    vent_count: int | None = Field(default=None, ge=0)
    starter_strip_lf: float | None = Field(default=None, ge=0)


def validate_geometry_dict(data: dict[str, Any]) -> None:
    """Validate geometry fields using deterministic mathematical rules."""
    from app.core.geometry_validation import (
        get_pitch_multiplier,
        validate_area_to_footprint,
        validate_edge_completeness,
        validate_pitch,
    )
    pitch_raw = data.get("predominant_pitch") or data.get("ev_predominant_pitch")
    if pitch_raw is not None:
        rise = validate_pitch(str(pitch_raw))
        multiplier = get_pitch_multiplier(rise)
    else:
        multiplier = 1.0

    total_area = float(data.get("total_area_sf") or data.get("ev_total_area_sf") or 0.0)
    eaves = float(data.get("eaves_lf") or data.get("ev_eaves_lf") or 0.0)
    rakes = float(data.get("rake_lf") or data.get("rakes_lf") or data.get("ev_rakes_lf") or 0.0)
    ridge = float(data.get("ridge_lf") or data.get("ev_ridge_lf") or 0.0)
    drip_edge = float(data.get("drip_edge_lf") or data.get("ev_drip_edge_lf") or 0.0)

    if total_area > 0:
        validate_edge_completeness(total_area, eaves, rakes, ridge)
        perimeter = (eaves + rakes) if (eaves + rakes) > 0 else (drip_edge if drip_edge > 0 else (eaves + rakes + ridge))
        validate_area_to_footprint(total_area, multiplier, perimeter)


@router.post(
    "/jobs/{job_id}/measurements/manual",
    response_class=JSONResponse,
    dependencies=[Depends(verify_office_role), Depends(check_rate_limit)]
)
async def manual_measurement_entry(
    request: Request,
    job_id: str,
    payload: ManualMeasurementPayload,
    role: str = Depends(get_current_role)
):
    """
    First-class manual measurement entry endpoint.
    Validates roof geometry using deterministic mathematical rules and transitions
    the job to EV_PARSED status without requiring an automated parse failure.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    payload_dict = payload.model_dump()
    try:
        validate_geometry_dict(payload_dict)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    conn = get_connection()
    try:
        cursor = conn.execute("SELECT id, status FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found.")

        db_updates: dict[str, Any] = {
            "ev_total_area_sf": payload.total_area_sf,
            "ev_predominant_pitch": payload.predominant_pitch,
            "ev_ridge_lf": payload.ridge_lf,
            "ev_hip_lf": payload.hip_lf,
            "ev_valley_lf": payload.valley_lf,
            "ev_eaves_lf": payload.eaves_lf,
            "ev_rakes_lf": payload.rake_lf,
            "ev_drip_edge_lf": payload.drip_edge_lf,
            "ev_flashing_lf": payload.flashing_lf,
            "ev_step_flashing_lf": payload.step_flashing_lf,
            "ev_flashing_wall_lf": payload.flashing_wall_lf,
            "ev_total_facets": payload.total_facets,
            "ev_pipe_boot_count": payload.pipe_boot_count,
            "ev_vent_count": payload.vent_count,
            "ev_starter_strip_lf": payload.starter_strip_lf,
            "pipeline_error_message": None
        }
        set_clause = ", ".join(f"{k} = ?" for k in db_updates)
        values = list(db_updates.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
        conn.commit()

        update_job_status(job_id, JobStatus.EV_PARSED, f"Measurements manually entered by {role}.")
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "Manual measurements saved and job marked EV_PARSED.",
        "job_id": job_id
    }


@router.post("/admin/triage/{job_id}/resolve",
             response_class=JSONResponse, dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def admin_triage_resolve(request: Request, job_id: str, payload: dict = Body(...), role: str = Depends(get_current_role)):
    """
    Accepts a dict of corrected geometry fields, validates them, writes them to
    the jobs table, resets status to EV_PARSED, and enqueues
    the ARQ worker to re-run from the reconcile step.
    """
    allowed_fields = {
        "ev_total_area_sf", "ev_predominant_pitch",
        "ev_ridge_lf", "ev_hip_lf", "ev_valley_lf",
        "ev_eaves_lf", "ev_rakes_lf", "ev_drip_edge_lf",
        "ev_flashing_lf", "ev_step_flashing_lf", "ev_flashing_wall_lf",
        "ev_total_facets", "ev_pipe_boot_count", "ev_vent_count",
        "ev_starter_strip_lf"
    }
    updates = {k: v for k, v in payload.items()
               if k in allowed_fields and v is not None}
    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No valid fields provided."
        )

    try:
        validate_geometry_dict(updates)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    conn = get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + ["EV_PARSED",
                      None, job_id]
        conn.execute(
            f"""UPDATE jobs
                SET {set_clause},
                    status = ?,
                    pipeline_error_message = ?
                WHERE id = ?""",
            values
        )
        conn.commit()
    finally:
        conn.close()

    # Re-enqueue the ARQ worker for this job (resume=True)
    redis = getattr(request.app.state, "redis_pool", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable — cannot re-queue job.")
    await redis.enqueue_job(
        "process_supplement_event",
        job_id=job_id,
        resume=True,
        role=role
    )
    return {"status": "queued", "job_id": job_id}


@router.patch(
    "/jobs/{job_id}/canvasser",
    response_class=JSONResponse,
    dependencies=[Depends(verify_admin)]
)
def reassign_canvasser(job_id: str, payload: dict = Body(...)):
    """
    Admin-only override to reassign canvasser_name.
    Used when a lead comes in through another channel and commission
    credit needs to be transferred to the originating rep.
    """
    name = payload.get("canvasser_name", "").strip()
    if not name:
        raise HTTPException(400, "canvasser_name must not be empty.")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            "UPDATE jobs SET canvasser_name = ? WHERE id = ?",
            (name, job_id)
        )
        if result.rowcount == 0:
            conn.execute("ROLLBACK")
            raise HTTPException(404, "Job not found.")
        conn.execute("COMMIT")
        logger.info("canvasser_reassigned", job_id=job_id, canvasser_name=name)
        return {"status": "updated", "job_id": job_id, "canvasser_name": name}
    except HTTPException:
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("canvasser_reassign_failed", job_id=job_id, error=str(e))
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/pipeline/summary", dependencies=[Depends(verify_admin)])
async def get_pipeline_summary():
    """
    Return an admin Sales Pipeline snapshot.

    Includes:
      - stage_counts: number of jobs in each pipeline stage
      - rep_metrics: per-canvasser lead / contingency / contract counts
      - avg_speed_to_lead_hours: average hours from lead capture to first advancement
      - total_active: total non-closed jobs
    """
    from app.core.database import get_sales_pipeline_summary
    try:
        summary = await asyncio.to_thread(get_sales_pipeline_summary)
        return summary
    except Exception as exc:
        logger.error("pipeline_summary_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch pipeline summary.")


class ReviewRequestPayload(BaseModel):
    requested_by: str = "office"


@router.post("/jobs/{job_id}/request-review", dependencies=[Depends(verify_office_role)])
async def office_request_review(job_id: str, payload: ReviewRequestPayload):
    """
    Admin/office: mark that a review (e.g. Google/Facebook) has been requested for this job.
    Idempotent — safe to call multiple times.
    """
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

    from app.core.database import request_review
    try:
        result = await asyncio.to_thread(request_review, job_id, payload.requested_by)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as exc:
        logger.error("office_request_review_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to record review request.")


class ReferralPayload(BaseModel):
    referral_code: str
    source: str = ""


@router.post("/jobs/{job_id}/referral", dependencies=[Depends(verify_office_role)])
async def office_add_referral(job_id: str, payload: ReferralPayload):
    """
    Admin/office: attach a referral code and source to a job.
    Idempotent — overwrites existing referral fields.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    from app.core.database import add_referral
    try:
        result = await asyncio.to_thread(add_referral, job_id, payload.referral_code, payload.source)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as exc:
        logger.error("office_add_referral_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to record referral.")

