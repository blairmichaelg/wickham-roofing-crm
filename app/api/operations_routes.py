"""
Operations-only restricted API routes.
Scott (Operations) can ONLY toggle material flags via this router.
He cannot access supplement data, financials, or job creation.
All routes require the ops-specific internal token.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.api.auth import verify_office_role, verify_operations
from app.core.database import (
    JobStatus,
    get_connection,
    insert_schedule,
    transition_material_flags,
    update_job_status,
)
from app.core.templates import templates
from app.services.pdf.documents import DocumentsGenerator

logger = structlog.get_logger("app.api.operations_routes")
router = APIRouter(prefix="/api/operations", tags=["operations"])

class MaterialFlagUpdate(BaseModel):
    """MaterialFlagUpdate definition."""
    materials_ordered: bool | None = None
    materials_on_site: bool | None = None


@router.patch("/job/{job_id}/materials", dependencies=[Depends(verify_operations)])
async def patch_material_flags(job_id: str, body: MaterialFlagUpdate):
    """
    The ONLY write endpoint Scott can reach. Toggles material
    confirmation flags. Drives MATERIALS_ON_SITE state transition.

    This endpoint is the sole mechanism by which INSTALL_SCHEDULED
    becomes unblocked — see Phase 1 state machine blocker.
    """
    # Validate UUID to prevent path injection
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    if body.materials_ordered is None and body.materials_on_site is None:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one flag: materials_ordered or materials_on_site.",
        )

    try:
        transition_material_flags(
            job_id=job_id,
            materials_ordered=body.materials_ordered,
            materials_on_site=body.materials_on_site,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info(
        "ops_material_flags_patched",
        job_id=job_id,
        ordered=body.materials_ordered,
        on_site=body.materials_on_site,
    )
    return {"status": "ok", "job_id": job_id}


@router.get(
    "/board",
    response_class=HTMLResponse,
    dependencies=[Depends(verify_office_role)]
)
async def operations_board(request: Request):
    """
    Operations Board functionality.
    
    Args:
            request (Request): request parameter.
    
    Returns:
        Any: The resulting output.
    """
    conn = get_connection()
    try:
        # List 1: Jobs needing materials ordered
        # (SUPPLEMENT_APPROVED, materials_ordered = 0)
        needs_materials = [dict(r) for r in conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name,
                   j.address_line1, j.city, j.state, j.status,
                   j.materials_ordered, j.materials_on_site,
                   j.ev_total_area_sf,
                   m.supplier_name, m.delivery_date,
                   m.bom_json
            FROM jobs j
            LEFT JOIN material_orders m ON j.id = m.job_id
            WHERE j.status = 'SUPPLEMENT_APPROVED'
              AND j.materials_ordered = 0
            ORDER BY j.created_at ASC
        """).fetchall()]

        # List 1.5: Jobs ordered but awaiting delivery
        # (materials_ordered = 1, materials_on_site = 0)
        awaiting_delivery_jobs = [dict(r) for r in conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name,
                   j.address_line1, j.city, j.state, j.status,
                   j.materials_ordered, j.materials_on_site,
                   j.ev_total_area_sf,
                   m.supplier_name, m.delivery_date,
                   m.bom_json
            FROM jobs j
            LEFT JOIN material_orders m ON j.id = m.job_id
            WHERE j.materials_ordered = 1 AND j.materials_on_site = 0
            ORDER BY j.created_at ASC
        """).fetchall()]

        # List 2: Jobs ready to schedule
        # (MATERIALS_ON_SITE, no crew date yet)
        ready_to_build = [dict(r) for r in conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name,
                   j.address_line1, j.city, j.state, j.status,
                   j.ev_total_area_sf,
                   s.crew_name, s.install_date
            FROM jobs j
            LEFT JOIN schedule s ON j.id = s.job_id
            WHERE j.status = 'MATERIALS_ON_SITE'
            ORDER BY j.created_at ASC
        """).fetchall()]

        # List 3: Active Builds (INSTALL_SCHEDULED)
        active_builds = [dict(r) for r in conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name,
                   j.address_line1, j.city, j.state, j.status,
                   j.ev_total_area_sf,
                   s.crew_name, s.install_date
            FROM jobs j
            LEFT JOIN schedule s ON j.id = s.job_id
            WHERE j.status = 'INSTALL_SCHEDULED'
            ORDER BY j.created_at ASC
        """).fetchall()]

        # List 4: Inspections & Closeout (INSTALL_COMPLETED, FINAL_INSPECTION, FINAL_INSPECTION_COMPLETED)
        inspections_closeout = [dict(r) for r in conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name,
                   j.address_line1, j.city, j.state, j.status,
                   j.ev_total_area_sf,
                   s.crew_name, s.install_date
            FROM jobs j
            LEFT JOIN schedule s ON j.id = s.job_id
            WHERE j.status IN ('INSTALL_COMPLETED', 'INSPECTION_COMPLETED', 'FINAL_INSPECTION', 'FINAL_INSPECTION_COMPLETED')
            ORDER BY j.created_at ASC
        """).fetchall()]

    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "operations_dashboard.html",
        {
            "needs_materials": needs_materials,
            "awaiting_delivery_jobs": awaiting_delivery_jobs,
            "ready_to_build": ready_to_build,
            "active_builds": active_builds,
            "inspections_closeout": inspections_closeout,
            "active_page": "operations",
            "auth_token": request.cookies.get("auth_token", "")
        }
    )

@router.post(
    "/jobs/{job_id}/schedule",
    response_class=JSONResponse,
    dependencies=[Depends(verify_operations)]
)
async def assign_crew(
    job_id: str, payload: dict = Body(...)
):
    """
    Assign Crew functionality.
    
    Args:
            job_id (str): job_id parameter.
            payload (dict): payload parameter.
    
    Returns:
        Any: The resulting output.
    """
    crew_name = payload.get("crew_name", "").strip()
    install_date = payload.get("install_date", "").strip()
    if not crew_name or not install_date:
        raise HTTPException(
            400,
            "crew_name and install_date are required."
        )
    insert_schedule(
        job_id=job_id,
        crew_name=crew_name,
        install_date=install_date,
        delivery_date=install_date,
        status="SCHEDULED"
    )
    update_job_status(
        job_id,
        JobStatus.INSTALL_SCHEDULED,
        f"Crew '{crew_name}' scheduled for {install_date}."
    )
    return {"status": "scheduled", "job_id": job_id}


@router.get(
    "/jobs/{job_id}/bom/download",
    dependencies=[Depends(verify_office_role)]
)
async def download_bom(job_id: str):
    import uuid
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(400, "Invalid job_id format.")
        
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Job not found.")
        job = dict(row)
    finally:
        conn.close()

    generator = DocumentsGenerator()
    try:
        filepath = await generator.generate_bom_pdf(job)
        filename = f"BOM_{job.get('invoice_id') or job_id[:8]}.pdf"
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=filename
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to generate BoM PDF: {e!s}")


@router.patch(
    "/jobs/{job_id}/status",
    dependencies=[Depends(verify_office_role)]
)
async def patch_job_status(job_id: str, payload: dict = Body(...)):
    new_status_str = payload.get("status")
    if not new_status_str:
        raise HTTPException(400, "status is required.")
    
    try:
        new_status = JobStatus(new_status_str)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {new_status_str}")
        
    allowed_statuses = {
        JobStatus.INSTALL_COMPLETED,
        JobStatus.FINAL_INSPECTION,
        JobStatus.FINAL_INSPECTION_COMPLETED
    }
    if new_status not in allowed_statuses:
        raise HTTPException(400, f"Status transition to {new_status_str} not allowed via this endpoint.")
        
    try:
        update_job_status(
            job_id,
            new_status,
            f"Status updated manually to {new_status_str} via Operations Board toggle."
        )
        return {"status": "success", "new_status": new_status_str}
    except Exception as e:
        raise HTTPException(400, str(e))
