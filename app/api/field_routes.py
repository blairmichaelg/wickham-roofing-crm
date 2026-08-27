"""
FastAPI HTTP surface for Field UX (iPad LAN decoupling).

These endpoints allow the field inspectors to:
1. Upload photos directly from the iPad over LAN (bypassing Google Drive sync).
2. Retrieve the InspectionJob summary (using cached Gemini analyses).
3. Capture and save digital signatures as physical images for the PDF appendix.
"""

import asyncio
import base64
import io
import json
import uuid
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, model_validator

from app.api.auth import get_current_claims, get_current_role, verify_field
from app.config import FIELD_DOCS_DIR
from app.core.cache import get_cached_analyses_for_job
from app.core.climate_lookup import is_ice_barrier_required
from app.core.database import get_connection, insert_job_document, update_job_status
from app.core.inspection_models import InspectionJob, get_stable_photos
from app.core.notifications import notifier
from app.core.upload_utils import stream_upload_safely
from app.services.field_access import assert_field_rep_owns_job
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.field_routes")
router = APIRouter(prefix="/api/field", tags=["field_ux"], dependencies=[Depends(verify_field)])

# Base directories (created on startup)
FIELD_PHOTOS_DIR = Path("field_photos")

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

class SignaturePayload(BaseModel):
    """SignaturePayload definition."""
    job_id: str = Field(..., description="Internal job identifier")
    signature_base64: str = Field(..., description="Data URI from HTML5 Canvas (data:image/png;base64,...)")
    ip_address: str | None = Field(None, description="IP address of the device capturing the signature")
    timestamp: str | None = Field(None, description="ISO8601 timestamp of signature capture")
    user_agent: str | None = Field(None, description="User Agent of the device capturing the signature")

class ContingencySignaturePayload(BaseModel):
    """ContingencySignaturePayload definition."""
    signature_base64: str = Field(..., description="Data URI from HTML5 Canvas")
    signer_name: str = Field(..., description="Name of the person signing")
    ip_address: str | None = Field(None, description="IP address of the device capturing the signature")
    user_agent: str | None = Field(None, description="User Agent of the device capturing the signature")

class RetailContractSignaturePayload(BaseModel):
    signature_base64: str
    signer_name: str
    ip_address: str | None = None
    user_agent: str | None = None
    total_price: float
    deposit_amount: float
    scope_description: str

    @model_validator(mode='after')
    def validate_pricing(self) -> 'RetailContractSignaturePayload':
        if self.total_price <= 0:
            raise ValueError("Total price must be greater than zero.")
        if self.deposit_amount < 0:
            raise ValueError("Deposit amount cannot be negative.")
        if self.deposit_amount > self.total_price:
            raise ValueError("Deposit amount cannot exceed total price.")
        return self


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
            "timestamp": datetime.now(__import__('datetime').timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "note": "Initial canvasser intake via Wickham Roofing CRM"
        }]
        
        conn.execute('''
            INSERT INTO jobs (
                id, invoice_id, homeowner_name, address_line1, city, state, postal_code, 
                phone, email, claim_number, insurer_name, status, status_history, job_type,
                ice_barrier_required, jurisdiction_code_version, canvasser_name, canvasser_rep_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            job_id, inv_id, payload.homeowner_name, payload.address_line1, payload.city,
            payload.state, payload.postal_code, payload.phone, payload.email,
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
    (FIELD_PHOTOS_DIR / job_id).mkdir(parents=True, exist_ok=True)
    (FIELD_DOCS_DIR / job_id).mkdir(parents=True, exist_ok=True)
    
    # Fork retail jobs to retail quote worker
    job_type = payload.job_type
    if job_type == "RETAIL":
        await request.app.state.redis_pool.enqueue_job(
            "process_retail_quote", job_id=job_id
        )

    logger.info("new_lead_captured", job_id=job_id, invoice_id=inv_id, homeowner=payload.homeowner_name)
    return {"status": "success", "job_id": job_id}


@router.post("/jobs/{job_id}/photos")
async def upload_field_photo(job_id: str, request: Request, file: UploadFile = File(...), claims: dict = Depends(get_current_claims)):
    """
    Upload Field Photo functionality.
    
    Args:
            job_id (str): job_id parameter.
            file (UploadFile): file parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    assert_field_rep_owns_job(claims, job_id)
    """
    Accept direct photo uploads from the iPad over LAN.
    Stores files in field_photos/{job_id}/ for downstream processing.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing")

    if file.content_type not in ["image/jpeg", "image/png"]:
        raise HTTPException(status_code=400, detail="Invalid image format. Must be JPEG or PNG.")

    job_dir = FIELD_PHOTOS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = Path(file.filename).name
    file_path = job_dir / safe_name

    try:
        file_hash = await stream_upload_safely(
            file, 
            file_path, 
            max_bytes=15 * 1024 * 1024, 
            allowed_magic_bytes=[b"\xFF\xD8\xFF", b"\x89PNG\r\n\x1A\n"]
        )
        logger.info("field_photo_uploaded", job_id=job_id, filename=safe_name, size=getattr(file, "size", 0))
        
        # Register photo in document vault immediately upon upload
        from app.core.database import insert_job_document
        await asyncio.to_thread(
            insert_job_document,
            job_id,
            safe_name,
            file.content_type or "image/jpeg",
            str(file_path),
            file_hash,
            "field_safe",
            "INSPECTION_PHOTO",
            False
        )

        # Trigger ARQ background damage analysis (Phase 1)
        redis = getattr(request.app.state, "redis_pool", None)
        if redis:
            await redis.enqueue_job("process_photo_damage", job_id, safe_name)
            
        return {"status": "success", "filename": safe_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("field_photo_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save photo")


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
async def get_field_job_details(job_id: str, claims: dict = Depends(get_current_claims)):
    """Retrieve full details of a specific job for field resumption."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")
    assert_field_rep_owns_job(claims, job_id)
    job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job_dict


@router.get("/jobs/{job_id}/docs/contingency")
async def download_unsigned_contingency(job_id: str, claims: dict = Depends(get_current_claims)):
    """Dynamically generates and returns an unsigned Insurance Contingency Agreement PDF for printing or emailing to homeowners."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    assert_field_rep_owns_job(claims, job_id)
    job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")

    from app.services.pdf import PDFGenerator
    pdf_gen = PDFGenerator()
    pdf_path = await pdf_gen.generate_contingency_agreement(job_dict)

    from app.services.security import sanitize_download_filename
    filename = sanitize_download_filename(f"Unsigned_Contingency_Agreement_{job_dict.get('homeowner_name', 'Job').replace(' ', '_')}.pdf")
    return FileResponse(path=pdf_path, filename=filename, media_type="application/pdf")



@router.post("/jobs/{job_id}/inspection-report", status_code=202)
async def trigger_inspection_report(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """Queue the inspection processor to build the homeowner inspection report from uploaded photos."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    assert_field_rep_owns_job(claims, job_id)

    redis = getattr(request.app.state, "redis_pool", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis connection unavailable")

    await redis.enqueue_job("process_inspection", job_id=job_id)
    return {"status": "accepted", "job_id": job_id, "message": "Inspection report generation started."}


@router.get("/jobs/{job_id}/inspection", response_model=InspectionJob)
async def get_inspection_summary(job_id: str, claims: dict | None = Depends(get_current_claims)):
    """
    Get Inspection Summary functionality.
    
    Args:
            job_id (str): job_id parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    if isinstance(claims, dict):
        assert_field_rep_owns_job(claims, job_id)
    """
    Retrieve the full InspectionJob summary.
    Constructs the job by scanning the local field_photos/{job_id} directory
    and reading available analyses directly from the SQLite cache.
    """
    job_dir = FIELD_PHOTOS_DIR / job_id

    # Get local photos if directory exists
    photos = []
    if job_dir.exists() and job_dir.is_dir():
        # Settle seconds = 0 for direct HTTP uploads (no Drive sync delay)
        photos = await asyncio.to_thread(get_stable_photos, job_dir, 0)
        
        # Ensure all photos are registered in the universal document vault for all roles
        if photos:
            def _sync_photos_to_vault():
                from app.core.database import insert_job_document
                for p in photos:
                    try:
                        insert_job_document(
                            job_id, p.filepath.name, "image/jpeg",
                            str(p.filepath), p.sha256, "field_safe", "INSPECTION_PHOTO", False
                        )
                    except Exception:
                        pass
            await asyncio.to_thread(_sync_photos_to_vault)

    # Retrieve all cached analyses for this job
    analyses = await asyncio.to_thread(get_cached_analyses_for_job, job_id)

    # Fetch real address and inspector from the jobs table
    property_address = "Unknown Address"
    inspector_name = "Wickham Roofing LLC"
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT address_line1, city, state, postal_code, inspector_name, canvasser_name FROM jobs WHERE id = ?",
            (job_id,)
        )
        row = cursor.fetchone()
        if row:
            property_address = f"{row['address_line1']}, {row['city']}, {row['state']} {row['postal_code']}"
            if row["inspector_name"]:
                inspector_name = row["inspector_name"]
            elif row["canvasser_name"]:
                inspector_name = row["canvasser_name"]
    finally:
        conn.close()

    job = InspectionJob(
        job_id=job_id,
        property_address=property_address,
        inspection_date=datetime.now(),
        inspector_name=inspector_name,
        photos=photos,
        analyses=analyses,
    )

    logger.info(
        "inspection_summary_retrieved",
        job_id=job_id,
        photos_count=job.total_photos,
        analyses_count=len(job.analyses),
    )
    return job


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
    assert_field_rep_owns_job(claims, job_id)
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
async def resolve_flag(job_id: str, flag_id: str, payload: FlagResolutionPayload, claims: dict = Depends(get_current_claims)):
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
    assert_field_rep_owns_job(claims, job_id)
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
async def update_field_claim_info(job_id: str, payload: FieldClaimInfoPayload, claims: dict = Depends(get_current_claims)):
    """
    Allow field reps/salesmen to update claim metadata (insurer, claim #, loss date, policy #, etc.)
    for jobs they own.
    """
    assert_field_rep_owns_job(claims, job_id)
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


@router.get("/jobs/{job_id}/inspection_report")
@router.post("/jobs/{job_id}/generate_report")
async def get_field_inspection_report(job_id: str, claims: dict = Depends(get_current_claims)):
    """
    Generate and download the Homeowner Inspection Report directly in the field app/tablet.
    Can be used by salesmen as a pitch & conversion tool BEFORE signing the contingency agreement.
    """
    assert_field_rep_owns_job(claims, job_id)
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

def _sync_process_image(encoded_b64: str, job_id: str, suffix: str = "contingency") -> Path:
    image_bytes = base64.b64decode(encoded_b64)
    image = Image.open(io.BytesIO(image_bytes))
    image.verify()  # Verify it's a valid image
    
    # Re-open for actual processing/saving since verify() leaves the file pointer at the end
    image = Image.open(io.BytesIO(image_bytes))
    
    # Enforce format and re-save cleanly
    if image.format not in ["PNG", "JPEG"]:
        raise ValueError("Unsupported image format")
        
    job_dir = FIELD_DOCS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    sig_file_path = job_dir / f"{job_id}_{suffix}_sig.png"
    
    # Convert to RGBA for PNG compatibility and save
    image = image.convert("RGBA")
    image.save(sig_file_path, format="PNG", optimize=True)
    return sig_file_path

def _sync_insert_agreement(agreement_id: str, job_id: str, pdf_path: str, sig_file_path: str, ts: str, signer_name: str, ip_address: str | None, user_agent: str | None):
    conn = get_connection()
    try:
        conn.execute('''
            INSERT INTO job_agreements (id, job_id, type, pdf_path, signature_image_path, signed_at, signed_by_name, signed_by_ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (agreement_id, job_id, "CONTINGENCY", pdf_path, sig_file_path, ts, signer_name, ip_address, user_agent))
        conn.commit()
    finally:
        conn.close()

@router.post("/jobs/{job_id}/contingency-sign")
async def contingency_sign(job_id: str, payload: ContingencySignaturePayload, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Contingency Sign functionality.
    
    Args:
            job_id (str): job_id parameter.
            payload (ContingencySignaturePayload): payload parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    assert_field_rep_owns_job(claims, job_id)
    """
    Handle E-Signature for Contingency Agreements.
    Saves PNG, generates PDF, logs agreement, and updates status.
    """
    # Strictly validate job_id format to prevent path traversal
    try:
        uuid_obj = uuid.UUID(job_id)
        job_id = str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    if len(payload.signature_base64) > 2_000_000:
        raise HTTPException(status_code=413, detail="Payload too large. Maximum size is 2MB.")
        
    if not payload.signature_base64.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=400, detail="Invalid signature format. Must be a PNG data URI.")
        
    try:
        job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)

        header, encoded = payload.signature_base64.split(",", 1)
        
        # Verify and sanitize the image using Pillow before saving to disk
        try:
            sig_file_path = await asyncio.to_thread(_sync_process_image, encoded, job_id)
        except Exception as e:
            logger.error("signature_image_verification_failed", error=str(e))
            raise HTTPException(status_code=400, detail="Invalid or corrupt image data")

        secure_ip = request.client.host if request.client else "Unknown IP"
        # Prefer X-Forwarded-For if behind a proxy
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            secure_ip = forwarded.split(",")[0].strip()
            
        secure_ua = request.headers.get("User-Agent", "Unknown UA")
        timestamp_utc = datetime.now(__import__('datetime').timezone.utc).replace(microsecond=0).isoformat() + "Z"

        from app.services.pdf import PDFGenerator
        pdf_gen = PDFGenerator()
        pdf_path = await pdf_gen.generate_contingency_pdf(
            job_dict, 
            str(sig_file_path), 
            payload.signer_name, 
            secure_ip,
            timestamp_utc
        )
        
        agreement_id = str(uuid.uuid4())
        import hashlib

        from app.core.database import insert_job_document
        
        def _insert_doc_and_agreement():
            _sync_insert_agreement(agreement_id, job_id, pdf_path, str(sig_file_path), timestamp_utc, payload.signer_name, secure_ip, secure_ua)
            with open(pdf_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            insert_job_document(job_id, Path(pdf_path).name, "CONTINGENCY_SIGNED", str(pdf_path), file_hash, "field_safe", "CONTINGENCY_SIGNED")
            update_job_status(job_id, "CONTINGENCY_SIGNED", f"Contingency signed by {payload.signer_name}")

        await asyncio.to_thread(_insert_doc_and_agreement)
        
        await notifier.broadcast({
            "type": "contingency_signed",
            "job": {
                "id": job_id,
                "signer_name": payload.signer_name,
                "status": "CONTINGENCY_SIGNED"
            }
        })
        
        logger.info("contingency_signed_and_generated", job_id=job_id, agreement_id=agreement_id)
        return {"status": "success", "pdf_path": Path(pdf_path).name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("contingency_sign_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process contingency signature")


@router.post("/jobs/{job_id}/sign-retail-contract")
async def sign_retail_contract(job_id: str, payload: RetailContractSignaturePayload, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Handle E-Signature for Retail Contracts.
    Saves PNG, generates PDF, logs agreement, and updates status.
    """
    assert_field_rep_owns_job(claims, job_id)

    try:
        uuid_obj = uuid.UUID(job_id)
        job_id = str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    if len(payload.signature_base64) > 2_000_000:
        raise HTTPException(status_code=413, detail="Payload too large. Maximum size is 2MB.")
        
    if not payload.signature_base64.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=400, detail="Invalid signature format. Must be a PNG data URI.")
        
    try:
        job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)

        header, encoded = payload.signature_base64.split(",", 1)
        
        # Verify and sanitize the image using Pillow before saving to disk
        try:
            sig_file_path = await asyncio.to_thread(_sync_process_image, encoded, job_id, "retail_contract")
        except Exception as e:
            logger.error("signature_image_verification_failed", error=str(e))
            raise HTTPException(status_code=400, detail="Invalid or corrupt image data")

        secure_ip = request.client.host if request.client else "Unknown IP"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            secure_ip = forwarded.split(",")[0].strip()
            
        secure_ua = request.headers.get("User-Agent", "Unknown UA")
        timestamp_utc = datetime.now(__import__('datetime').timezone.utc).replace(microsecond=0).isoformat() + "Z"

        from app.services.pdf.documents import DocumentsGenerator
        pdf_gen = DocumentsGenerator()
        
        total_price_cents = int(payload.total_price * 100)
        deposit_cents = int(payload.deposit_amount * 100)
        
        pdf_path = await pdf_gen.generate_retail_contract_pdf(
            job=job_dict, 
            signature_path=str(sig_file_path), 
            signer_name=payload.signer_name, 
            ip_address=secure_ip,
            total_price_cents=total_price_cents,
            deposit_cents=deposit_cents,
            scope_description=payload.scope_description,
            timestamp_utc=timestamp_utc
        )
        
        noc_pdf_path = await pdf_gen.generate_retail_notice_of_cancellation(job=job_dict)
        
        agreement_id = str(uuid.uuid4())
        import hashlib

        from app.core.database import insert_job_document
        
        def _insert_docs_and_agreement():
            _sync_insert_agreement(agreement_id, job_id, pdf_path, str(sig_file_path), timestamp_utc, payload.signer_name, secure_ip, secure_ua)
            
            with open(pdf_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            insert_job_document(job_id, Path(pdf_path).name, "RETAIL_CONTRACT_SIGNED", str(pdf_path), file_hash, "field_safe", "RETAIL_CONTRACT_SIGNED")
            
            with open(noc_pdf_path, "rb") as f:
                noc_file_hash = hashlib.sha256(f.read()).hexdigest()
            insert_job_document(job_id, Path(noc_pdf_path).name, "RETAIL_NOTICE_OF_CANCELLATION", str(noc_pdf_path), noc_file_hash, "field_safe", "RETAIL_NOTICE_OF_CANCELLATION")
            
            update_job_status(job_id, "RETAIL_CONTRACT_SIGNED", f"Retail contract signed by {payload.signer_name}")

        await asyncio.to_thread(_insert_docs_and_agreement)
        
        await notifier.broadcast({
            "type": "retail_contract_signed",
            "job": {
                "id": job_id,
                "signer_name": payload.signer_name,
                "status": "RETAIL_CONTRACT_SIGNED"
            }
        })
        
        logger.info("retail_contract_signed_and_generated", job_id=job_id, agreement_id=agreement_id)
        return {"status": "success", "pdf_path": Path(pdf_path).name, "noc_pdf_path": Path(noc_pdf_path).name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("retail_contract_sign_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process retail contract signature")


@router.get("/storms/{zipcode}")
async def get_zip_storms(zipcode: str, role: str = Depends(verify_field)):
    """Fetch recent storm events for a given zip code for field sales reps."""
    from datetime import UTC, datetime

    from app.config import get_settings
    settings = get_settings()
    min_hail = settings.storm_alert_min_hail_inches
    min_wind = settings.storm_alert_min_wind_mph

    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT event_date, event_type, MAX(hail_size_inches) as hail_size_inches, MAX(wind_speed_mph) as wind_speed_mph 
            FROM storm_events 
            WHERE zipcode = ?
              AND (
                (event_type = 'HAIL' AND hail_size_inches >= ?)
                OR
                (event_type = 'WIND' AND wind_speed_mph >= ?)
                OR
                (event_type NOT IN ('HAIL', 'WIND'))
              )
            GROUP BY event_date, event_type 
            ORDER BY event_date DESC 
            LIMIT 5
            """,
            (zipcode.strip(), min_hail, min_wind)
        )
        raw_events = [dict(r) for r in cursor.fetchall()]
        formatted_events = []
        for e in raw_events:
            raw_type = str(e.get("event_type", "")).upper()
            hail = e.get("hail_size_inches") or 0.0
            wind = e.get("wind_speed_mph") or 0.0

            if "HAIL" in raw_type or hail > 0:
                label = "Hail Event"
                metric = f"{hail:.2f}\" Hail" if hail > 0 else "Hail Verified"
                badge_class = "bg-amber-900/80 text-amber-300 border-amber-600"
            elif "GST" in raw_type:
                label = "Severe Wind Gust"
                metric = f"{wind:.0f} mph Gust" if wind > 0 else "Severe Gust"
                badge_class = "bg-blue-900/80 text-blue-300 border-blue-600"
            else:
                label = "Thunderstorm Wind Damage"
                metric = f"{wind:.0f} mph Wind" if wind > 0 else "Wind Damage"
                badge_class = "bg-red-900/80 text-red-300 border-red-600"

            e["display_label"] = label
            e["display_metric"] = metric
            e["badge_class"] = badge_class
            e["formatted_date"] = str(e.get("event_date", ""))[:10]
            formatted_events.append(e)

        cursor_ref = conn.execute("SELECT MAX(ingested_at) FROM storm_events")
        row_ref = cursor_ref.fetchone()
        last_refreshed = row_ref[0] if (row_ref and row_ref[0]) else datetime.now(UTC).isoformat()

        return {"events": formatted_events, "last_refreshed_utc": last_refreshed}
    finally:
        conn.close()


@router.get("/jobs/{job_id}/documents")
async def get_field_job_documents(job_id: str, claims: dict = Depends(get_current_claims)):
    """
    Fetch list of field-safe documents for a job owned by the field representative.
    """
    try:
        uuid_obj = uuid.UUID(job_id)
        job_id = str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    assert_field_rep_owns_job(claims, job_id)

    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT id, job_id, filename, file_type, category, visibility, created_at
               FROM job_documents
               WHERE job_id = ? AND visibility = 'field_safe'
               ORDER BY created_at DESC""",
            (job_id,)
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


@router.get("/jobs/{job_id}/documents/{doc_id}/download")
async def download_field_job_document(job_id: str, doc_id: str, claims: dict = Depends(get_current_claims)):
    """
    Download a field-safe document from the Document Vault.
    Strictly checks job ownership and field_safe visibility.
    """
    try:
        job_id = str(uuid.UUID(job_id))
        doc_id = str(uuid.UUID(doc_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id or doc_id format. Must be a valid UUID.")

    assert_field_rep_owns_job(claims, job_id)

    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT storage_path, filename, file_type, visibility FROM job_documents WHERE id = ? AND job_id = ?",
            (doc_id, job_id)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found.")

        if row["visibility"] != "field_safe":
            raise HTTPException(status_code=403, detail="Not authorized to view this document.")

        path = Path(row["storage_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="File is missing from disk.")

        from app.services.security import sanitize_download_filename
        return FileResponse(
            path,
            media_type=row["file_type"] or "application/octet-stream",
            filename=sanitize_download_filename(row["filename"])
        )
    finally:
        conn.close()


@router.get("/jobs/{job_id}/evidence_grid")
async def download_field_evidence_grid(job_id: str, claims: dict = Depends(get_current_claims)):
    """Download the field-safe Inspection Evidence Grid from the field API namespace."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    assert_field_rep_owns_job(claims, job_id)

    from app.api.office_routes import download_evidence_grid
    return await download_evidence_grid(job_id)


# ============================================================
# SALES INTELLIGENCE FIELD ENDPOINTS  (Steps 3–5)
# ============================================================

class FieldReviewRequestPayload(BaseModel):
    requested_by: str = ""


@router.post("/jobs/{job_id}/request-review")
async def field_request_review(
    job_id: str,
    payload: FieldReviewRequestPayload,
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

    assert_field_rep_owns_job(claims, job_id)

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

    assert_field_rep_owns_job(claims, job_id)

    from app.core.database import add_referral
    try:
        result = await asyncio.to_thread(add_referral, job_id, payload.referral_code, payload.source)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as exc:
        logger.error("field_add_referral_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to record referral.")


@router.get("/jobs/{job_id}/docs/neighbor-letter")
async def get_neighbor_letter(job_id: str, claims: dict = Depends(get_current_claims)):
    """
    Generate (or retrieve from vault) the neighbor outreach letter for a completed job.
    Only available once the job has reached INSTALL_COMPLETED or later.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    assert_field_rep_owns_job(claims, job_id)

    conn = get_connection()
    try:
        job_row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found.")
        job = dict(job_row)

        VALID_STATUSES = {
            "INSTALL_COMPLETED", "FINAL_INSPECTION", "FINAL_INSPECTION_COMPLETED",
            "INVOICED", "CLOSED", "SUPPLEMENT_APPROVED", "SCOPE_APPROVED",
        }
        if job.get("status") not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Neighbor letter is only available for completed jobs (current status: {job.get('status')})."
            )

        # Check vault for an existing letter
        existing = conn.execute(
            "SELECT storage_path FROM job_documents WHERE job_id = ? AND category = 'NEIGHBOR_LETTER' ORDER BY created_at DESC LIMIT 1",
            (job_id,)
        ).fetchone()
    finally:
        conn.close()

    if existing and Path(existing["storage_path"]).exists():
        from app.services.security import sanitize_download_filename
        return FileResponse(
            path=existing["storage_path"],
            filename=sanitize_download_filename(f"Neighbor_Letter_{job_id[:8]}.pdf"),
            media_type="application/pdf",
        )

    # Fetch nearby storm events for context
    from app.core.database import get_storm_events_near_job
    storm_events = await asyncio.to_thread(
        get_storm_events_near_job,
        job_id=job_id,
        window_hours=168,  # 1 week
    )

    from app.services.pdf.neighbor_letter import NeighborLetterGenerator
    gen = NeighborLetterGenerator()
    pdf_path = await gen.generate(job, storm_events)

    # Register in vault
    await asyncio.to_thread(
        insert_job_document,
        job_id,
        f"Neighbor_Letter_{job_id[:8]}.pdf",
        "application/pdf",
        pdf_path,
        None,
        "field_safe",
        "NEIGHBOR_LETTER",
        True,
    )

    from app.services.security import sanitize_download_filename
    return FileResponse(
        path=pdf_path,
        filename=sanitize_download_filename(f"Neighbor_Letter_{job_id[:8]}.pdf"),
        media_type="application/pdf",
    )


@router.get("/jobs/{job_id}/sales-tools")
async def get_sales_tools(job_id: str, claims: dict = Depends(get_current_claims)):
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

    assert_field_rep_owns_job(claims, job_id)

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
