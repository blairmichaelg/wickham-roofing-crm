"""
FastAPI HTTP surface for the Office Control Center Dashboard (V4 Strike 3).
Handles job retrieval, EagleView uploads, and generated artifact downloads.
"""

import asyncio
import csv
import io
import json
import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic import BaseModel

from app.api.auth import (
    get_current_claims,
    get_current_role,
    verify_accounting,
    verify_admin,
    verify_field,
    verify_office_role,
)
from app.api.field_routes import get_inspection_summary
from app.config import FIELD_DOCS_DIR
from app.core.backup import backup_database
from app.core.database import (
    JobStatus,
    _fetch_job_sync,
    atomic_qbo_export,
    get_connection,
    get_financials,
    get_job_document_by_hash,
    insert_job_document,
    insert_material_order,
    insert_schedule,
    update_job_status,
    upsert_financials,
)
from app.core.job_costing import compute_job_profitability
from app.core.pipeline import run_full_office_pipeline, run_supplement_pipeline
from app.core.templates import templates
from app.core.upload_utils import stream_upload_safely
from app.services.hover_extractor import detect_pdf_format
from app.services.pdf import PDFGenerator
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.office_routes")
router = APIRouter(prefix="/api/office", tags=["office_ux"])
EXPORT_DIR = Path("generated_exports")

def _fetch_homeowner_name_sync(job_id: str) -> str:
    """
    Fetch the homeowner's name for a given job synchronously.

    Args:
        job_id (str): The unique identifier of the job.

    Returns:
        str: The homeowner's name or 'Unknown Customer' if not found.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT homeowner_name FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        return str(row["homeowner_name"]) if row else "Unknown Customer"
    finally:
        conn.close()



class FinancialsPayload(BaseModel):
    """FinancialsPayload definition."""
    revenue: float
    carrier_rcv: float
    materials: float
    labor: float
    deductible: float = 0.0
    acv_payment: float = 0.0
    recoverable_depreciation: float = 0.0
    overhead_pct: float = 0.25
    commission_pct: float = 0.10
    permits_fee: float = 0.0

class ProductionPayload(BaseModel):
    """ProductionPayload definition."""
    supplier_name: str
    delivery_date: str
    crew_name: str
    install_date: str

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

class ManualFlashingPayload(BaseModel):
    """ManualFlashingPayload definition."""
    flashing_lf: float
    step_flashing_lf: float

class MaterialOrderPayload(BaseModel):
    """MaterialOrderPayload definition."""
    supplier_name: str
    delivery_date: str

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


@router.post("/jobs/{job_id}/eagleview", dependencies=[Depends(verify_admin)])
async def upload_eagleview(job_id: str, file: UploadFile = File(...)):
    """
    Trigger the V4 Automath pipeline.
    Saves PDF, extracts metrics, calculates BOM, generates QBO CSV, and updates status.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Must upload a PDF file.")

    job_dir = FIELD_DOCS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job_dir / "eagleview.pdf"

    # 1. Save File & Get Hash
    try:
        file_hash = await stream_upload_safely(
            file, 
            pdf_path,
            max_bytes=25 * 1024 * 1024,
            allowed_magic_bytes=[b"%PDF-"]
        )
        logger.info("eagleview_pdf_uploaded", job_id=job_id, size=getattr(file, "size", 0), sha256=file_hash)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("eagleview_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save EagleView PDF")



    # Format Detection Route
    try:
        fmt = detect_pdf_format(pdf_path)
        if fmt == "UNKNOWN":
            pdf_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Unknown measurement PDF format. Must be EagleView or Hover.")
    except Exception as e:
        pdf_path.unlink(missing_ok=True)
        if isinstance(e, HTTPException): raise
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Check for duplicate hash
    existing_doc = await asyncio.to_thread(get_job_document_by_hash, job_id, file_hash)
    if existing_doc:
        logger.warning("idempotent_upload_prevented", job_id=job_id, filename="eagleview.pdf", sha256=file_hash)
        pdf_path.unlink(missing_ok=True)
        return {"status": "success", "message": "Duplicate file detected. Skipped pipeline.", "pipeline_result": None}

    # 3. Get Homeowner Name for QBO
    try:
        homeowner_name = await asyncio.to_thread(_fetch_homeowner_name_sync, job_id)
    except Exception as e:
        logger.error("eagleview_homeowner_fetch_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch homeowner name")

    # 4. Trigger Master Orchestrator
    try:
        result = await run_full_office_pipeline(job_id, pdf_path, customer_name=homeowner_name)
        # Register document with hash
        await asyncio.to_thread(insert_job_document, job_id, pdf_path.name, "application/pdf", str(pdf_path), file_hash, "field_safe", "MEASUREMENT_REPORT")
    except Exception as e:
        import traceback
        logger.error("master_pipeline_failed_route", job_id=job_id, error=traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pipeline Orchestration Failed: {e!s}")

    return {"status": "success", "message": "Master Pipeline complete, QBO CSV generated.", "pipeline_result": result}


@router.post("/jobs/{job_id}/supplement_docs", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def upload_supplement_docs(
    request: Request,
    job_id: str, 
    ev_file: UploadFile = File(...), 
    sol_file: UploadFile = File(...),
    role: str = Depends(get_current_role)
):
    """
    Upload both EagleView and Statement of Loss PDFs to trigger the Supplement pipeline.
    Injects the background task directly into the ARQ queue.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    if ev_file.content_type != "application/pdf" or sol_file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Both files must be PDFs.")

    job_dir = FIELD_DOCS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    ev_path = job_dir / "eagleview.pdf"
    sol_path = job_dir / "statement_of_loss.pdf"

    try:
        ev_hash = await stream_upload_safely(ev_file, ev_path, max_bytes=25 * 1024 * 1024, allowed_magic_bytes=[b"%PDF-"])
        sol_hash = await stream_upload_safely(sol_file, sol_path, max_bytes=25 * 1024 * 1024, allowed_magic_bytes=[b"%PDF-"])
        
        logger.info("supplement_docs_uploaded", job_id=job_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("supplement_docs_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save PDFs")



    try:
        fmt = detect_pdf_format(ev_path)
        if fmt == "UNKNOWN":
            ev_path.unlink(missing_ok=True)
            sol_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Unknown measurement PDF format. Must be EagleView or Hover.")
    except Exception as e:
        ev_path.unlink(missing_ok=True)
        sol_path.unlink(missing_ok=True)
        if isinstance(e, HTTPException): raise
        raise HTTPException(status_code=400, detail=str(e))

    try:
        ev_sha256 = ev_hash   # Already computed by stream_upload_safely
        sol_sha256 = sol_hash # Already computed by stream_upload_safely
        
        meas_cat = "HOVER_REPORT" if fmt == "HOVER" else ("EAGLEVIEW_REPORT" if fmt == "EAGLEVIEW" else "MEASUREMENT_REPORT")
        meas_type = "HOVER_PDF" if fmt == "HOVER" else ("EAGLEVIEW_PDF" if fmt == "EAGLEVIEW" else "MEASUREMENT_PDF")
        meas_name = ev_file.filename if ev_file.filename else ev_path.name

        # Insert or update documents in vault
        ev_doc_id = await asyncio.to_thread(
            insert_job_document, job_id, meas_name, meas_type, str(ev_path), ev_sha256, "field_safe", meas_cat, True
        )
        sol_doc_id = await asyncio.to_thread(
            insert_job_document, job_id, sol_path.name, "SOL_PDF", str(sol_path), sol_sha256, "office_only", "STATEMENT_OF_LOSS", True
        )

        redis = getattr(request.app.state, "redis_pool", None)
        if redis:
            await redis.enqueue_job(
                "process_supplement_event",
                job_id=job_id,
                ev_pdf_path=str(ev_path),
                sol_pdf_path=str(sol_path),
                ev_sha256=ev_sha256,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_sha256,
                sol_doc_id=sol_doc_id,
                generate_pdf=True,
                role=role
            )
        else:
            await run_supplement_pipeline(
                job_id=job_id,
                ev_pdf_path=str(ev_path),
                sol_pdf_path=str(sol_path),
                ev_sha256=ev_sha256,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_sha256,
                sol_doc_id=sol_doc_id,
                generate_pdf=True,
                ctx={"role": role},
            )
        
        logger.info("supplement_task_enqueued", job_id=job_id)
    except Exception as e:
        logger.error("supplement_enqueue_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to queue supplement task")

    return {"status": "success", "message": "Supplement generation enqueued."}


@router.get("/jobs/{job_id}/evidence_grid", dependencies=[Depends(verify_field)])
async def download_evidence_grid(job_id: str):
    """
    Builds the InspectionJob from local filesystem and cache,
    generates the ReportLab PDF Evidence Grid, and returns the file download.
    Accessible to core team and field reps.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    try:
        # Fetch job record for clean naming & summary
        conn = get_connection()
        try:
            row = conn.execute("SELECT homeowner_name, address_line1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            homeowner_name = row["homeowner_name"] if row and row["homeowner_name"] else "Inspection"
            address_line1 = row["address_line1"] if row and row["address_line1"] else ""
        finally:
            conn.close()

        # Build a fresh summary before serving from the vault so newly cached
        # Gemini analyses and newly uploaded photos can invalidate old grids.
        job = await get_inspection_summary(job_id)

        # Check if Evidence Grid is already in the document vault
        conn = get_connection()
        try:
            existing_doc = conn.execute(
                """SELECT storage_path FROM job_documents
                   WHERE job_id = ? AND category = 'EVIDENCE_GRID'
                   ORDER BY created_at DESC LIMIT 1""",
                (job_id,)
            ).fetchone()
            if existing_doc and Path(existing_doc["storage_path"]).exists() and not job.analyses:
                logger.info("evidence_grid_retrieved_from_vault", job_id=job_id, path=existing_doc["storage_path"])
                pdf_path = existing_doc["storage_path"]
            else:
                pdf_path = None
        finally:
            conn.close()

        # Human-readable filename
        h_clean = "".join(c for c in homeowner_name if c.isalnum() or c in (" ", "_")).strip().replace(" ", "_")
        a_clean = "".join(c for c in address_line1 if c.isalnum() or c in (" ", "_")).strip().replace(" ", "_")
        if h_clean and a_clean:
            out_name = f"{h_clean}_{a_clean}_Inspection_Evidence_Grid.pdf"
        elif h_clean:
            out_name = f"{h_clean}_Inspection_Evidence_Grid.pdf"
        else:
            out_name = f"Evidence_Grid_{job_id[:8]}.pdf"

        if not pdf_path:
            # Look for signature
            sig_path_c = FIELD_DOCS_DIR / job_id / f"{job_id}_contingency_sig.png"
            sig_path_r = FIELD_DOCS_DIR / job_id / f"{job_id}_retail_contract_sig.png"
            if sig_path_c.exists():
                signature_to_pass = str(sig_path_c)
            elif sig_path_r.exists():
                signature_to_pass = str(sig_path_r)
            else:
                signature_to_pass = None

            # Generate PDF
            pdf_gen = PDFGenerator()
            pdf_path = await pdf_gen.generate_evidence_grid(job, signature_to_pass)

            # Register/vault the generated document
            await asyncio.to_thread(
                insert_job_document,
                job_id, out_name, "application/pdf",
                str(pdf_path), None, "field_safe", "EVIDENCE_GRID", True
            )
        
        return FileResponse(
            path=pdf_path,
            filename=out_name,
            media_type="application/pdf"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("evidence_grid_download_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to generate Evidence Grid.")


@router.get("/jobs/{job_id}/docs/download/{doc_id}")
def download_job_document(
    job_id: str, 
    doc_id: str, 
    role: str = Depends(get_current_role), 
    claims: dict = Depends(get_current_claims)
):
    """
    Download a file from the Universal Document Vault.
    Enforces RBAC: Field reps cannot access financial or office-only documents.
    """
    try:
        job_id = str(uuid.UUID(job_id))
        doc_id = str(uuid.UUID(doc_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id or doc_id format.")

    from app.api.field_routes import assert_field_rep_owns_job
    if role == "field":
        assert_field_rep_owns_job(claims, job_id)

    conn = get_connection()
    try:
        cursor = conn.execute("SELECT storage_path, filename, file_type, visibility FROM job_documents WHERE id = ? AND job_id = ?", (doc_id, job_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found.")
            
        if role == "field":
            if row["visibility"] != "field_safe":
                raise HTTPException(status_code=403, detail="Not authorized to view this document.")
        
        path = Path(row["storage_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="File is missing from disk.")
            
        from app.services.security import sanitize_download_filename
        return FileResponse(path, media_type=row["file_type"], filename=sanitize_download_filename(row["filename"]))
    finally:
        conn.close()


@router.get("/download/{filename}", dependencies=[Depends(verify_admin)])
def download_export(filename: str):
    """
    Download a generated CSV or PDF from the exports directory.
    """
    from app.services.security import sanitize_download_filename
    
    clean_filename = sanitize_download_filename(filename)
    file_path = EXPORT_DIR / clean_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
        
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream"
    )

@router.post("/jobs/{job_id}/docs/upload", dependencies=[Depends(verify_admin)])
async def upload_job_document(job_id: str, file_type: str = Form(...), file: UploadFile = File(...)):
    """Upload a miscellaneous document to the universal vault."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    valid_types = ["application/pdf", "image/jpeg", "image/png"]
    actual_type = file.content_type
    if actual_type not in valid_types:
        raise HTTPException(status_code=400, detail="Must upload a PDF, JPEG, or PNG.")

    job_dir = FIELD_DOCS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize and assign a safe filename
    safe_name = Path(file.filename or "unknown").name
    pdf_path = job_dir / safe_name

    try:
        file_hash = await stream_upload_safely(
            file, 
            pdf_path,
            max_bytes=25 * 1024 * 1024,
            allowed_magic_bytes=[b"%PDF-", b"\xFF\xD8\xFF", b"\x89PNG\r\n\x1A\n"]
        )
        
        from app.core.database import get_job_document_by_hash
        existing_doc = await asyncio.to_thread(get_job_document_by_hash, job_id, file_hash)
        if existing_doc:
            logger.warning("idempotent_upload_prevented", job_id=job_id, filename=safe_name, sha256=file_hash)
            pdf_path.unlink(missing_ok=True)
            return {"status": "success", "filename": safe_name, "message": "Duplicate file detected."}
            
        try:
            category = file_type.upper() if file_type else "UNSPECIFIED"
            visibility = "field_safe" if category in ["HOVER_REPORT", "MEASUREMENT_REPORT", "PHOTO"] else "office_only"
            
            await asyncio.to_thread(insert_job_document, job_id, safe_name, actual_type, str(pdf_path), file_hash, visibility, category)
        except Exception:
            pdf_path.unlink(missing_ok=True)
            raise
            
        logger.info("job_document_uploaded", job_id=job_id, filename=safe_name, size=getattr(file, "size", 0), sha256=file_hash)
        return {"status": "success", "filename": safe_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("job_document_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save document")

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
            from app.core.database import JobStatus, _fetch_job_sync, update_job_status
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


@router.get("/jobs/{job_id}/docs/inspection_letter", dependencies=[Depends(verify_admin)])
async def get_inspection_letter(job_id: str):
    """
    Generate and download the Homeowner Inspection Report PDF based on field photo analysis.
    Does NOT require EagleView measurement data.
    """
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
                logger.info("homeowner_report_retrieved_from_vault", job_id=job_id, path=existing_doc["storage_path"])
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
        logger.error("inspection_letter_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to generate Inspection Letter")

@router.get("/jobs/{job_id}/qbo_export", dependencies=[Depends(verify_admin)])
def download_qbo_export(job_id: str):
    """Returns the generated QBO CSV for the given job."""
    csv_path = EXPORT_DIR / f"INV-{job_id[:8].upper()}_QBO.csv"
    
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="QBO Export not found for this job.")
        
    return FileResponse(
        path=csv_path,
        filename=f"INV-{job_id[:8].upper()}_QBO.csv",
        media_type="text/csv"
    )

def _sync_update_job_financials(job_id: str, payload: FinancialsPayload):
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT commission_pct_override FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        override = job_row["commission_pct_override"] if job_row else None
    finally:
        conn.close()

    # Calculate precise financials
    results = compute_job_profitability(
        revenue_cents=int(round(payload.revenue * 100)),
        materials_cents=int(round(payload.materials * 100)),
        labor_cents=int(round(payload.labor * 100)),
        overhead_pct=payload.overhead_pct,
        commission_pct=payload.commission_pct,
        commission_pct_override=override
    )
    
    # Directive 4: Low Margin Alert
    if results["gross_margin"] < 0.35:
        logger.warning(
            "low_margin_alert", 
            job_id=job_id, 
            gross_margin=results["gross_margin"],
            revenue=payload.revenue,
            direct_costs=results["direct_costs_cents"] / 100.0
        )
        
    # Store raw parameters in DB
    upsert_financials(
        job_id=job_id,
        revenue_cents=int(round(payload.revenue * 100)),
        carrier_rcv_cents=int(round(payload.carrier_rcv * 100)),
        material_cost_cents=int(round(payload.materials * 100)),
        labor_cost_cents=int(round(payload.labor * 100)),
        overhead_pct=payload.overhead_pct,
        canvasser_commission_pct=payload.commission_pct,
        permits_fee_cents=int(round(payload.permits_fee * 100)),
        deductible_cents=int(round(payload.deductible * 100)),
        acv_payment_cents=int(round(payload.acv_payment * 100)),
        recoverable_depreciation_cents=int(round(payload.recoverable_depreciation * 100)),
    )
    
    # Convert returned integer cents back to dollars for API response
    return {
        "direct_costs": results["direct_costs_cents"] / 100.0,
        "gross_profit": results["gross_profit_cents"] / 100.0,
        "gross_margin": results["gross_margin"],
        "overhead_cost": results["overhead_cost_cents"] / 100.0,
        "net_profit": results["net_profit_cents"] / 100.0,
        "canvasser_commission": results["canvasser_commission_cents"] / 100.0,
        "effective_commission_pct": results["effective_commission_pct"]
    }

@router.post("/jobs/{job_id}/financials", dependencies=[Depends(verify_accounting)])
async def update_job_financials(job_id: str, payload: FinancialsPayload, bg_tasks: BackgroundTasks):
    """
    Process pre-build job costing parameters from the Office Dashboard.
    Calculates exact margin profiles and logs alerts if profitability is too low.
    """
    try:
        results = await asyncio.to_thread(_sync_update_job_financials, job_id, payload)
        
        # Trigger Hot Backup
        bg_tasks.add_task(backup_database)
        
        return {"status": "success", "financials": results}
    except Exception as e:
        logger.error("job_costing_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to calculate and save financials.")


def _sync_update_job_production(job_id: str, payload: ProductionPayload):
    # Dummy BOM JSON for now, in a real scenario we'd pull the actual calculated BOM
    dummy_bom = json.dumps({"status": "scheduled_for_delivery"})
    
    insert_material_order(
        job_id=job_id,
        supplier_name=payload.supplier_name,
        delivery_date=payload.delivery_date,
        bom_json=dummy_bom
    )
    
    insert_schedule(
        job_id=job_id,
        crew_name=payload.crew_name,
        install_date=payload.install_date,
        delivery_date=payload.delivery_date,
        status="SCHEDULED"
    )
    
    update_job_status(
        job_id,
        JobStatus.MATERIAL_ORDERED,
        f"Material order placed with {payload.supplier_name}, delivery {payload.delivery_date}"
    )

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
            import uuid
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



@router.post("/jobs/{job_id}/production", dependencies=[Depends(verify_admin)])
async def update_job_production(job_id: str, payload: ProductionPayload, bg_tasks: BackgroundTasks):
    """
    Unified route to set both material orders and installation schedule.
    Transitions job to MATERIAL_ORDERED. Operations must confirm MATERIALS_ON_SITE before INSTALL_SCHEDULED becomes valid.
    """
    try:
        await asyncio.to_thread(_sync_update_job_production, job_id, payload)
        
        bg_tasks.add_task(backup_database)
        
        return {"status": "success", "message": "Production scheduled."}
    except Exception as e:
        logger.error("production_update_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to schedule production.")

@router.post("/jobs/{job_id}/material_order", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def generate_material_order(job_id: str, payload: MaterialOrderPayload, bg_tasks: BackgroundTasks):
    """
    Triggers the generation of the supplier PO and updates job status to MATERIAL_ORDERED.
    """
    try:
        from app.core.pipeline import generate_material_order_pipeline
        await generate_material_order_pipeline(job_id, payload.supplier_name, payload.delivery_date)
        bg_tasks.add_task(backup_database)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("material_order_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process material order")

@router.post("/jobs/{job_id}/manual_flashing", dependencies=[Depends(verify_admin)])
def manual_flashing(job_id: str, payload: ManualFlashingPayload):
    """Saves manual flashing entry and makes job eligible for pipeline retry."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE jobs SET flashing_lf = ?, step_flashing_lf = ? WHERE id = ?",
            (payload.flashing_lf, payload.step_flashing_lf, job_id)
        )
        if conn.total_changes == 0:
            raise HTTPException(status_code=404, detail="Job not found")
            
        # Re-triggering the pipeline implies the job should move back to a state that allows it.
        # But per requirements: "After persisting, the job should be eligible for pipeline re-trigger."
        conn.execute("COMMIT")
        return {"status": "success", "message": "Manual flashing entry saved."}
    except HTTPException:
        conn.execute("ROLLBACK")
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("manual_flashing_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save manual flashing.")
    finally:
        conn.close()

@router.get("/jobs/{job_id}/docs/po", dependencies=[Depends(verify_admin)])
def download_po(job_id: str, supplier_name: str):
    """Returns the generated Material Purchase Order PDF."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    from app.services.security import sanitize_download_filename
    safe_name = sanitize_download_filename(supplier_name.replace(' ', '_'))
    po_path = FIELD_DOCS_DIR / job_id / f"PO_{safe_name}.pdf"
    
    if not po_path.exists():
        raise HTTPException(status_code=404, detail="Purchase Order not found.")
        
    return FileResponse(path=po_path, filename=f"PO_{safe_name}.pdf", media_type="application/pdf")

@router.get("/jobs/{job_id}/docs/cancellation", dependencies=[Depends(verify_admin)])
async def download_cancellation(job_id: str):
    """Dynamically generates and returns the Georgia Notice of Cancellation."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    job_dict = await asyncio.to_thread(_fetch_job_sync, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")
        
    pdf_gen = PDFGenerator()
    pdf_path = await pdf_gen.generate_notice_of_cancellation(job_dict)
    
    return FileResponse(path=pdf_path, filename=f"Notice_of_Cancellation_{job_id[:8]}.pdf", media_type="application/pdf")

@router.get("/jobs/{job_id}/docs/completion", dependencies=[Depends(verify_admin)])
async def download_completion(job_id: str, completion_date: str):
    """Dynamically generates and returns the Certificate of Completion."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    job_dict = await asyncio.to_thread(_fetch_job_sync, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")
        
    pdf_gen = PDFGenerator()
    pdf_path = await pdf_gen.generate_certificate_of_completion(job_dict, completion_date)
    
    return FileResponse(path=pdf_path, filename=f"Certificate_of_Completion_{job_id[:8]}.pdf", media_type="application/pdf")

@router.get("/jobs/{job_id}/docs/contingency", dependencies=[Depends(verify_admin)])
async def download_contingency(job_id: str):
    """Dynamically generates and returns the Insurance Contingency Agreement."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    job_dict = await asyncio.to_thread(_fetch_job_sync, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")
        
    pdf_gen = PDFGenerator()
    pdf_path = await pdf_gen.generate_contingency_agreement(job_dict)
    
    return FileResponse(path=pdf_path, filename=f"Contingency_Agreement_{job_id[:8]}.pdf", media_type="application/pdf")

class MaterialRow(BaseModel):
    """MaterialRow definition."""
    job_id: str
    homeowner_name: str
    supplier_name: str
    delivery_date: str
    materials_ordered: int
    materials_on_site: int
    status: str

class OperationsBrief(BaseModel):
    """OperationsBrief definition."""
    deliveries_today: int
    crews_today: int
    material_rows: list[MaterialRow]

@router.get("/operations/brief", response_model=OperationsBrief, dependencies=[Depends(verify_admin)])
def get_operations_brief():
    """Zero-click read projection for operations dashboard."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT m.job_id, j.homeowner_name, m.supplier_name, m.delivery_date,
                   j.materials_ordered, j.materials_on_site, j.status AS job_status
            FROM material_orders m
            JOIN jobs j ON m.job_id = j.id
        """)
        m_rows = cursor.fetchall()
        
        material_rows = []
        deliveries_today = 0
        import datetime
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for r in m_rows:
            d_date = r["delivery_date"]
            if d_date == today_str:
                deliveries_today += 1
            material_rows.append(MaterialRow(
                job_id=r["job_id"],
                homeowner_name=r["homeowner_name"],
                supplier_name=r["supplier_name"],
                delivery_date=d_date,
                materials_ordered=r["materials_ordered"],
                materials_on_site=r["materials_on_site"],
                status=r["job_status"]
            ))
            
        cursor = conn.execute("SELECT COUNT(*) as crews FROM schedule WHERE install_date LIKE ?", (f"{today_str}%",))
        c_row = cursor.fetchone()
        crews_today = c_row["crews"] if c_row else 0
        
        return OperationsBrief(
            deliveries_today=deliveries_today,
            crews_today=crews_today,
            material_rows=material_rows
        )
    finally:
        conn.close()

class AccountingBrief(BaseModel):
    """AccountingBrief definition."""
    supplemented_rcv_added: str
    qbo_ready_count: int
    rows: list[dict[str, Any]]

@router.get("/accounting/brief", response_model=AccountingBrief, dependencies=[Depends(verify_accounting)])
def get_accounting_brief():
    """Zero-click read projection for accounting dashboard."""
    conn = get_connection()
    try:
        # Sum RCV for all active pipeline jobs (non-CLOSED)
        cursor = conn.execute("""
            SELECT COALESCE(SUM(f.carrier_rcv_cents), 0) as total_rcv_cents
            FROM financials f
            JOIN jobs j ON j.id = f.job_id
            WHERE j.status IN (
                'SUPPLEMENT_GENERATED', 'SUPPLEMENT_SUBMITTED', 'AWAITING_CARRIER_RESPONSE',
                'SUPPLEMENT_APPROVED', 'SUPPLEMENT_DENIED', 'MATERIAL_ORDERED', 'MATERIALS_ON_SITE',
                'INSTALL_SCHEDULED', 'INSTALL_COMPLETED', 'FINAL_INSPECTION', 'INSPECTION_COMPLETED',
                'FINAL_INSPECTION_COMPLETED', 'INVOICED', 'PAYMENT_RECEIVED',
                'EV_ORDERED', 'ACV_PAYMENT_RECEIVED', 'DEPRECIATION_PAYMENT_RECEIVED', 'RETAIL_PAYMENT_RECEIVED'
            )
        """)
        rcv_row = cursor.fetchone()
        supplemented_rcv = f"${(rcv_row['total_rcv_cents'] / 100.0):,.2f}"
        
        # Get count of jobs awaiting QBO export
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM jobs j
            JOIN financials f ON j.id = f.job_id
            WHERE j.status IN ('SUPPLEMENT_APPROVED', 'INVOICED', 'ACV_PAYMENT_RECEIVED', 'DEPRECIATION_PAYMENT_RECEIVED', 'RETAIL_PAYMENT_RECEIVED')
              AND f.qbo_exported = 0
        """)
        qbo_ready = cursor.fetchone()["cnt"]

        # Fetch active pipeline jobs
        cursor = conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name, j.status, j.job_type,
                   j.acv_received, j.acv_received_at,
                   j.supplement_received, j.supplement_received_at,
                   f.carrier_rcv_cents, f.recoverable_depreciation_cents,
                   f.qbo_exported, f.acv_payment_received_at, f.depreciation_payment_received_at,
                   f.retail_payment_received_at, f.deductible_paid, f.deductible_paid_cents, f.deductible_cents
            FROM jobs j
            LEFT JOIN financials f ON j.id = f.job_id
            WHERE j.status IN (
                'SUPPLEMENT_GENERATED', 'SUPPLEMENT_SUBMITTED', 'AWAITING_CARRIER_RESPONSE',
                'SUPPLEMENT_APPROVED', 'SUPPLEMENT_DENIED', 'MATERIAL_ORDERED', 'MATERIALS_ON_SITE',
                'INSTALL_SCHEDULED', 'INSTALL_COMPLETED', 'FINAL_INSPECTION', 'INSPECTION_COMPLETED',
                'FINAL_INSPECTION_COMPLETED', 'INVOICED', 'PAYMENT_RECEIVED',
                'EV_ORDERED', 'ACV_PAYMENT_RECEIVED', 'DEPRECIATION_PAYMENT_RECEIVED', 'RETAIL_PAYMENT_RECEIVED'
            )
            ORDER BY j.created_at ASC
        """)
        active_rows = cursor.fetchall()
        
        # Fetch last 5 completed jobs
        cursor = conn.execute("""
            SELECT j.id, j.invoice_id, j.homeowner_name, j.status, j.job_type,
                   j.acv_received, j.acv_received_at,
                   j.supplement_received, j.supplement_received_at,
                   f.carrier_rcv_cents, f.recoverable_depreciation_cents,
                   f.qbo_exported, f.acv_payment_received_at, f.depreciation_payment_received_at,
                   f.retail_payment_received_at, f.deductible_paid, f.deductible_paid_cents, f.deductible_cents
            FROM jobs j
            LEFT JOIN financials f ON j.id = f.job_id
            WHERE j.status = 'CLOSED'
            ORDER BY j.created_at DESC
            LIMIT 5
        """)
        closed_rows = cursor.fetchall()
        
        rows = list(active_rows) + list(closed_rows)
        
        acct_rows = []
        for r in rows:
            recoverable_dep = (r["recoverable_depreciation_cents"] or 0) / 100.0
            carrier_rcv = (r["carrier_rcv_cents"] or 0) / 100.0
            if recoverable_dep and recoverable_dep > 0:
                acv_expected = carrier_rcv - recoverable_dep
                supp_expected = recoverable_dep
            else:
                acv_expected = None
                supp_expected = None
            
            acct_rows.append({
                "job_id": r["id"],
                "invoice_id": r["invoice_id"],
                "name": r["homeowner_name"],
                "status": r["status"],
                "job_type": r["job_type"] or "insurance",
                "acv_received": r["acv_received"],
                "acv_received_at": r["acv_received_at"],
                "supplement_received": r["supplement_received"],
                "supplement_received_at": r["supplement_received_at"],
                "acv_expected": acv_expected,
                "supp_expected": supp_expected,
                "carrier_rcv": carrier_rcv,
                "qbo_exported": bool(r["qbo_exported"]) if r["qbo_exported"] is not None else False,
                "acv_payment_received_at": r["acv_payment_received_at"],
                "depreciation_payment_received_at": r["depreciation_payment_received_at"],
                "retail_payment_received_at": r["retail_payment_received_at"],
                "deductible_paid": bool(r["deductible_paid"]) if r["deductible_paid"] is not None else False,
                "deductible_paid_cents": r["deductible_paid_cents"] or 0,
                "deductible_cents": r["deductible_cents"] or 0
            })
        
        return AccountingBrief(
            supplemented_rcv_added=supplemented_rcv,
            qbo_ready_count=qbo_ready,
            rows=acct_rows
        )
    finally:
        conn.close()



@router.get("/accounting/qbo-export", dependencies=[Depends(verify_accounting)])
async def export_qbo_csv(token=Depends(verify_accounting)):
    """
    Batch QBO export. Queries all eligible jobs (qbo_exported=0),
    generates CSV, sets idempotency lock, returns file download.
    Returns 204 with message if no jobs are pending export.
    """
    batch = atomic_qbo_export()
    if not batch:
        from fastapi import Response
        return Response(
            content="No jobs pending QBO export.",
            status_code=204
        )

    import datetime
    today_dt = datetime.datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    due_date_str = (today_dt + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    output = io.StringIO()
    fieldnames = [
        "*Customer",
        "*InvoiceDate",
        "*DueDate",
        "Terms",
        "Item(Product/Service)",
        "ItemQuantity",
        "ItemRate",
        "ItemAmount",
        "Memo"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for job in batch:
        qbo_row = {
            "*Customer":             job["homeowner_name"],
            "*InvoiceDate":          today_str,
            "*DueDate":              due_date_str,
            "Terms":                 "Net 30",
            "Item(Product/Service)": "Roofing Services",
            "ItemQuantity":          1,
            "ItemRate":              job["carrier_rcv_cents"] / 100.0,
            "ItemAmount":            job["carrier_rcv_cents"] / 100.0,
            "Memo":                  f"Invoice {job.get('invoice_id','N/A')} | "
                                     f"Claim {job.get('claim_number','N/A')}"
        }
        writer.writerow(qbo_row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=wickham_qbo_export.csv"
        }
    )

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
                       ORDER BY CASE jt.task_type WHEN 'SUPPLEMENT_DRAFTING' THEN 0 ELSE 1 END
                       LIMIT 1
                   ) AS last_error,
                   j.ev_total_area_sf, j.ev_predominant_pitch,
                   j.ev_ridge_lf, j.ev_hip_lf,
                   j.ev_valley_lf, j.ev_eaves_lf, j.ev_rakes_lf
            FROM jobs j
            WHERE j.status = 'PENDING_OPERATOR_REVIEW'
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

@router.post("/admin/triage/{job_id}/resolve",
             response_class=JSONResponse, dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def admin_triage_resolve(request: Request, job_id: str, payload: dict = Body(...), role: str = Depends(get_current_role)):
    """
    Accepts a dict of corrected geometry fields, writes them to
    the jobs table, resets status to EV_PARSED, and enqueues
    the ARQ worker to re-run from the reconcile step.
    """
    allowed_fields = {
        "ev_total_area_sf", "ev_predominant_pitch",
        "ev_ridge_lf", "ev_hip_lf", "ev_valley_lf",
        "ev_eaves_lf", "ev_rakes_lf"
    }
    updates = {k: v for k, v in payload.items()
               if k in allowed_fields and v is not None}
    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No valid fields provided."
        )
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

@router.post(
    "/jobs/{job_id}/trigger-supplement",
    response_class=JSONResponse,
    dependencies=[Depends(verify_office_role)]
)
async def trigger_supplement_route(request: Request, job_id: str, claims: dict = Depends(get_current_claims)):
    """Manually trigger or regenerate supplement pipeline for a job."""
    from app.core.pipeline import _fetch_latest_report_sync, run_supplement_pipeline
    role = claims.get("role", "admin")

    # Locate measurement and statement of loss documents for this job
    conn = get_connection()
    try:
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
            await run_supplement_pipeline(
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

    from app.services.security import sanitize_download_filename
    filename = sanitize_download_filename(f"Supplement_Request_{job_id[:8]}.pdf")
    return FileResponse(path=pdf_path, media_type="application/pdf", filename=filename)




@router.post(
    "/jobs/{job_id}/approve-supplement",
    response_class=JSONResponse
, dependencies=[Depends(verify_admin)])
async def approve_supplement(
    job_id: str, payload: dict = Body(default={})
):
    """
    Operator gate: transitions AWAITING_CARRIER_RESPONSE
    -> SUPPLEMENT_APPROVED.
    Triggers a WebSocket broadcast to alert Scott and Debi.
    """
    note = payload.get("note", "Approved by operator.")
    from app.core.database import JobStatus, update_job_status
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
    response_class=JSONResponse
, dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
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
    from app.core.database import JobStatus, update_job_status
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
    response_class=FileResponse
, dependencies=[Depends(verify_admin)])
async def download_rebuttal(job_id: str):
    """
    Download Rebuttal functionality.
    
    Args:
            job_id (str): job_id parameter.
    
    Returns:
        Any: The resulting output.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    from fastapi.responses import FileResponse

    from app.core.database import get_job_documents
    docs = get_job_documents(job_id,
                             file_type="REBUTTAL_PDF")
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

@router.get("/accounting/commissions-ready", response_class=JSONResponse, dependencies=[Depends(verify_accounting)])
def get_commissions_ready():
    """
    Get Commissions Ready functionality.
    
    Returns:
        Any: The resulting output.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT j.id as job_id, j.invoice_id, j.homeowner_name, j.canvasser_name, j.commission_generated_at,
                   j.commission_pct_override, f.revenue_cents, f.canvasser_commission_pct
            FROM jobs j
            LEFT JOIN financials f ON j.id = f.job_id
            WHERE j.commission_ready = 1
            ORDER BY j.commission_generated_at DESC
        """)
        results = []
        for r in cursor.fetchall():
            row = dict(r)
            effective_pct = row["commission_pct_override"] if row["commission_pct_override"] is not None else row["canvasser_commission_pct"]
            if effective_pct is None: 
                effective_pct = 0.10
            revenue = (row["revenue_cents"] or 0) / 100.0
            row["canvasser_commission"] = revenue * effective_pct
            results.append(row)
        return results
    finally:
        conn.close()

@router.get("/jobs/{job_id}/docs/commission", response_class=FileResponse, dependencies=[Depends(verify_accounting)])
def download_commission(job_id: str):
    """
    Download Commission functionality.
    
    Args:
            job_id (str): job_id parameter.
    
    Returns:
        Any: The resulting output.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    from app.core.database import get_job_documents
    docs = get_job_documents(job_id, file_type="COMMISSION_PDF")
    if not docs:
        raise HTTPException(404, "No commission statement found.")
    path = docs[0]["storage_path"]
    if not Path(path).exists():
        raise HTTPException(404, "Commission PDF missing.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"Commission_Statement_{job_id[:8]}.pdf"
    )

@router.post("/jobs/{job_id}/escalate", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def queue_escalation(request: Request, job_id: str):
    """
    Queue Escalation functionality.
    
    Args:
            request (Request): request parameter.
            job_id (str): job_id parameter.
    
    Returns:
        Any: The resulting output.
    """
    await request.app.state.redis_pool.enqueue_job(
        "process_escalation",
        job_id=job_id
    )
    return {"status": "escalation_queued"}

@router.get("/jobs/{job_id}/docs/escalation", response_class=FileResponse, dependencies=[Depends(verify_admin)])
def download_escalation(job_id: str):
    """
    Download Escalation functionality.
    
    Args:
            job_id (str): job_id parameter.
    
    Returns:
        Any: The resulting output.
    """
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

class TogglePaymentPayload(BaseModel):
    flag: str
    amount: float
    date_received: str

@router.post("/accounting/jobs/{job_id}/toggle-payment", dependencies=[Depends(verify_accounting)])
async def toggle_payment_route(job_id: str, payload: TogglePaymentPayload, request: Request):
    from app.core.database import toggle_payment_flag, update_job_status
    try:
        amount_cents = int(round(payload.amount * 100))
        result = toggle_payment_flag(job_id, payload.flag, amount_cents, payload.date_received)
        if result.get("commission_triggered"):
            # Update status to PAYMENT_RECEIVED
            await asyncio.to_thread(update_job_status, job_id, "PAYMENT_RECEIVED", "Both ACV and Supplement checks received.")
            await request.app.state.redis_pool.enqueue_job(
                "process_commission",
                job_id=job_id
            )
        return {"status": "success"}
    except Exception as e:
        logger.error("toggle_payment_failed", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

class MarkPaymentPayload(BaseModel):
    payment_type: str
    amount: float | None = None
    date_received: str | None = None
    deductible_paid: bool | None = None

@router.post("/accounting/jobs/{job_id}/mark-payment", dependencies=[Depends(verify_accounting)])
async def mark_payment_route(job_id: str, payload: MarkPaymentPayload, request: Request):
    from app.core.database import record_financial_payment
    try:
        await asyncio.to_thread(
            record_financial_payment,
            job_id,
            payload.payment_type,
            payload.amount,
            payload.date_received,
            payload.deductible_paid
        )
        # Trigger commission job if both ACV and Depreciation payments are received
        conn = get_connection()
        try:
            fin_row = conn.execute(
                "SELECT acv_payment_received_at, depreciation_payment_received_at FROM financials WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            if fin_row and fin_row["acv_payment_received_at"] and fin_row["depreciation_payment_received_at"]:
                await request.app.state.redis_pool.enqueue_job(
                    "process_commission",
                    job_id=job_id
                )
        finally:
            conn.close()
        return {"status": "success"}
    except Exception as e:
        logger.error("mark_payment_failed", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

class CommissionOverridePayload(BaseModel):
    commission_pct: float | None

@router.post("/accounting/jobs/{job_id}/commission-override", dependencies=[Depends(verify_accounting)])
def commission_override_route(job_id: str, payload: CommissionOverridePayload):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE jobs SET commission_pct_override = ? WHERE id = ?", (payload.commission_pct, job_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Job not found")
        conn.execute("COMMIT")
        return {"status": "success"}
    except HTTPException:
        conn.execute("ROLLBACK")
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("commission_override_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save commission override")
    finally:
        conn.close()

@router.post("/accounting/jobs/{job_id}/invoice", dependencies=[Depends(verify_accounting)])
async def create_invoice_route(job_id: str, bg_tasks: BackgroundTasks):
    """
    Transition a job to INVOICED status, generating an invoice number and
    making it visible in the QBO export queue.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    conn = get_connection()
    try:
        row = conn.execute("SELECT status, invoice_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found.")
        
        current_status = row["status"]
        valid_for_invoice = [
            "SUPPLEMENT_APPROVED", "SCOPE_APPROVED", "MATERIAL_ORDERED",
            "MATERIALS_ON_SITE", "INSTALL_SCHEDULED", "INSTALL_COMPLETED",
            "FINAL_INSPECTION", "INSPECTION_COMPLETED", "FINAL_INSPECTION_COMPLETED",
            "SUPPLEMENT_GENERATED"
        ]
        if current_status not in valid_for_invoice:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot invoice from status '{current_status}'. Job must be at least SUPPLEMENT_APPROVED or INSTALL_COMPLETED."
            )
    finally:
        conn.close()

    try:
        await asyncio.to_thread(update_job_status, job_id, "INVOICED", "Invoice created from Accounting Dashboard")
        bg_tasks.add_task(backup_database)
        return {"status": "success", "message": "Job transitioned to INVOICED status."}
    except Exception as e:
        logger.error("invoice_creation_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create invoice: {e!s}")

@router.patch("/accounting/jobs/{job_id}/commission/paid", dependencies=[Depends(verify_accounting)])
async def mark_commission_paid(job_id: str, bg_tasks: BackgroundTasks):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE jobs SET commission_ready = 0 WHERE id = ?", (job_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Job not found")
        conn.execute("COMMIT")
    except HTTPException:
        conn.execute("ROLLBACK")
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("mark_commission_paid_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to mark commission paid")
    finally:
        conn.close()

    try:
        conn = get_connection()
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        if row and row["status"] == "PAYMENT_RECEIVED":
            await asyncio.to_thread(update_job_status, job_id, "CLOSED", "Commission paid. Job closed/archived.")
        bg_tasks.add_task(backup_database)
        return {"status": "success"}
    except Exception as e:
        logger.error("mark_commission_paid_status_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to transition job status: {e!s}")


# ============================================================
# SALES INTELLIGENCE ENDPOINTS  (Steps 1–3)
# ============================================================

@router.get("/storms/targets", dependencies=[Depends(verify_office_role)])
async def get_storm_canvassing_targets(
    window_hours: int = 72,
    limit: int = 10,
):
    """
    Return the top-N canvassing target areas ranked by storm severity.

    Query params:
      - window_hours (int, default 72): look-back window in hours.
      - limit (int, default 10): maximum number of target areas returned.

    Returns a list of dicts with location, severity, hail/wind stats, and event count.
    Accessible to all authenticated users (field reps and office staff).
    """
    from app.config import get_settings
    from app.core.database import get_connection
    from app.services.canvassing_targets import get_ranked_canvassing_targets
    
    settings = get_settings()
    conn = get_connection()
    try:
        row = conn.execute("SELECT MAX(ingested_at) FROM storm_events").fetchone()
        last_refreshed = row[0] if (row and row[0]) else None
    finally:
        conn.close()

    try:
        targets = await asyncio.to_thread(
            get_ranked_canvassing_targets,
            window_hours=window_hours,
            limit=limit,
        )
        return {
            "targets": targets,
            "window_hours": window_hours,
            "count": len(targets),
            "min_hail": settings.storm_alert_min_hail_inches,
            "min_wind": settings.storm_alert_min_wind_mph,
            "last_refreshed_utc": last_refreshed,
        }
    except Exception as exc:
        logger.error("storm_targets_fetch_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch storm canvassing targets.")


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
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
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
