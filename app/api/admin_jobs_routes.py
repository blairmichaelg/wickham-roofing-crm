"""
Admin Job Management API.

Provides emergency override endpoints for managing jobs.
All endpoints are admin-only.
"""

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.auth import verify_admin
from app.core.database import force_override_status

logger = structlog.get_logger("app.api.admin_jobs")
router = APIRouter(
    prefix="/api/admin/jobs",
    tags=["admin-jobs"],
    dependencies=[Depends(verify_admin)]
)

@router.post("/{job_id}/override", response_class=JSONResponse, status_code=200)
def override_job_status(
    job_id: str,
    payload: dict = Body(...),
):
    """
    Emergency override to forcefully transition a job's status.
    Body: {"new_status": "CLOSED", "note": "Override for specific reason."}
    """
    new_status = payload.get("new_status", "").strip()
    note = payload.get("note", "").strip()
    
    if not new_status:
        raise HTTPException(status_code=400, detail="new_status is required.")
    if not note:
        raise HTTPException(status_code=400, detail="A reason (note) is required for all overrides.")
        
    try:
        force_override_status(job_id=job_id, new_status=new_status, note=note)
        return {"status": "success", "message": f"Job {job_id} overridden to {new_status}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("api_override_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


