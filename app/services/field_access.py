import structlog
from fastapi import HTTPException

from app.core.database import get_connection

logger = structlog.get_logger("app.services.field_access")

def assert_field_rep_owns_job(claims: dict, job_id: str) -> None:
    """
    Ensure the requesting field rep owns or has permission for the specified job.
    Admins/operations/accounting and core team members bypass this check.
    If a job is explicitly assigned to another rep_id, access is restricted to that rep.
    Unassigned jobs or jobs assigned to the requesting rep are fully accessible.
    Raises 403 Forbidden if unauthorized.
    """
    if not claims or not isinstance(claims, dict):
        return
    role = claims.get("role")
    if role in ["admin", "operations", "accounting"]:
        return
        
    rep_name = (claims.get("rep_name") or claims.get("sub") or "").strip().lower()
    if rep_name in {"michael", "scott", "debi", "alex wickham"}:
        return

    field_rep_id = claims.get("rep_id")
    
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT canvasser_rep_id, canvasser_name FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Job not found.")
            
        job_rep_id = row["canvasser_rep_id"]
        job_rep_name = row["canvasser_name"]
        
        # If job is unassigned, all field reps / salesmen can access & generate reports
        if not job_rep_id and not job_rep_name:
            return

        # If job is assigned to a specific rep_id, check ownership
        if job_rep_id:
            if field_rep_id and str(job_rep_id) == str(field_rep_id):
                return
            if rep_name and job_rep_name and rep_name.lower() in job_rep_name.lower():
                return
            # Assigned to another rep -> Deny
            logger.warning("field_rep_access_denied", job_id=job_id, field_rep_id=field_rep_id, job_rep_id=job_rep_id)
            raise HTTPException(
                status_code=403, 
                detail="Not authorized to access this job."
            )

        # If canvasser_name exists but no rep_id
        if rep_name and job_rep_name and rep_name.lower() in job_rep_name.lower():
            return
            
        # Fallback for field/salesman on unassigned/name-only jobs
        if role in ["field", "salesman"]:
            return

        raise HTTPException(
            status_code=403, 
            detail="Not authorized to access this job."
        )
    finally:
        conn.close()
