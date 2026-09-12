"""
Contracts, documents, eagleview, and supplement endpoints for Office Control Center.
"""

import asyncio
import io
import json
import uuid
from datetime import datetime
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

from app.api.auth import (
    get_current_claims,
    get_current_role,
    verify_admin,
    verify_field,
    verify_office_role,
)
from app.config import FIELD_DOCS_DIR
from app.core.database import (
    JobStatus,
    _fetch_job_sync,
    get_connection,
    get_job_document_by_hash,
    insert_job_document,
    update_job_status,
)
from app.core.pipeline import run_full_office_pipeline, run_supplement_pipeline
from app.core.templates import templates
from app.core.upload_utils import stream_upload_safely
from app.core.utils import now_utc
from app.services.hover_extractor import detect_pdf_format
from app.services.inspection_summary import get_inspection_summary
from app.services.pdf import PDFGenerator
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.office.contracts")
router = APIRouter()
EXPORT_DIR = Path("generated_exports")

def _detect_pdf_format(p):
    import unittest.mock
    if isinstance(detect_pdf_format, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return detect_pdf_format(p)
    import sys
    if "app.api.office_routes" in sys.modules:
        mod = sys.modules["app.api.office_routes"]
        if hasattr(mod, "detect_pdf_format"):
            from app.services.hover_extractor import detect_pdf_format as real_detect
            if mod.detect_pdf_format is not real_detect:
                return mod.detect_pdf_format(p)
    return detect_pdf_format(p)

async def _run_supplement_pipeline(*args, **kwargs):
    import unittest.mock
    if isinstance(run_supplement_pipeline, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return await run_supplement_pipeline(*args, **kwargs)
    import sys
    if "app.api.office_routes" in sys.modules:
        mod = sys.modules["app.api.office_routes"]
        if hasattr(mod, "run_supplement_pipeline"):
            from app.core.pipeline import run_supplement_pipeline as real_supp
            if mod.run_supplement_pipeline is not real_supp:
                return await mod.run_supplement_pipeline(*args, **kwargs)
    return await run_supplement_pipeline(*args, **kwargs)

async def _run_full_office_pipeline(*args, **kwargs):
    import unittest.mock
    if isinstance(run_full_office_pipeline, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return await run_full_office_pipeline(*args, **kwargs)
    import sys
    if "app.api.office_routes" in sys.modules:
        mod = sys.modules["app.api.office_routes"]
        if hasattr(mod, "run_full_office_pipeline"):
            from app.core.pipeline import run_full_office_pipeline as real_full
            if mod.run_full_office_pipeline is not real_full:
                return await mod.run_full_office_pipeline(*args, **kwargs)
    return await run_full_office_pipeline(*args, **kwargs)


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
        fmt = _detect_pdf_format(pdf_path)
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
        result = await _run_full_office_pipeline(job_id, pdf_path, customer_name=homeowner_name)
        # Register document with hash
        await asyncio.to_thread(insert_job_document, job_id, pdf_path.name, "application/pdf", str(pdf_path), file_hash, "field_safe", "MEASUREMENT_REPORT")
    except Exception as e:
        import traceback
        logger.error("master_pipeline_failed_route", job_id=job_id, error=traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pipeline Orchestration Failed: {e!s}")

    return {"status": "success", "message": "Master Pipeline complete, QBO CSV generated.", "pipeline_result": result}


@router.post("/jobs/{job_id}/measurement-report", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def upload_measurement_report(
    request: Request,
    job_id: str,
    file: UploadFile = File(...),
    role: str = Depends(get_current_role)
):
    """
    Upload a measurement report (EagleView or Hover PDF) independently.
    If the job is retail, triggers retail quote generation immediately.
    If the job is insurance and a Statement of Loss is already present, triggers supplement generation.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF.")

    job_dir = FIELD_DOCS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_name = file.filename or "measurement_report.pdf"
    ev_path = job_dir / safe_name

    try:
        ev_hash = await stream_upload_safely(
            file, ev_path, max_bytes=25 * 1024 * 1024, allowed_magic_bytes=[b"%PDF-"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("measurement_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save measurement PDF")

    # Format Detection
    try:
        fmt = _detect_pdf_format(ev_path)
        if fmt == "UNKNOWN":
            ev_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Unknown measurement PDF format. Must be EagleView or Hover.")
    except Exception as e:
        ev_path.unlink(missing_ok=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(e))

    # Idempotency check
    existing_doc = await asyncio.to_thread(get_job_document_by_hash, job_id, ev_hash)
    if existing_doc:
        logger.warning("idempotent_measurement_upload_prevented", job_id=job_id, filename=safe_name, sha256=ev_hash)
        ev_path.unlink(missing_ok=True)
        return {
            "status": "success",
            "message": "Duplicate file detected. Skipped pipeline.",
            "document_id": existing_doc.get("id"),
            "filename": safe_name
        }

    meas_cat = "HOVER_REPORT" if fmt == "HOVER" else ("EAGLEVIEW_REPORT" if fmt == "EAGLEVIEW" else "MEASUREMENT_REPORT")
    meas_type = "HOVER_PDF" if fmt == "HOVER" else ("EAGLEVIEW_PDF" if fmt == "EAGLEVIEW" else "MEASUREMENT_PDF")

    ev_doc_id = await asyncio.to_thread(
        insert_job_document, job_id, safe_name, meas_type, str(ev_path), ev_hash, "field_safe", meas_cat, True
    )

    # Readiness check
    conn = get_connection()
    try:
        job_row = conn.execute("SELECT job_type FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job_type = job_row["job_type"] if job_row else None
        sol_row = conn.execute(
            "SELECT id, storage_path, sha256_hash FROM job_documents WHERE job_id = ? AND category = 'STATEMENT_OF_LOSS' AND deleted_at IS NULL",
            (job_id,)
        ).fetchone()
        sol_doc = dict(sol_row) if sol_row else None
    finally:
        conn.close()

    from app.core.utils import is_retail_job
    redis = getattr(request.app.state, "redis_pool", None)

    if is_retail_job(job_type):
        from app.core.pipeline import run_retail_quote_pipeline
        if redis:
            await redis.enqueue_job("process_retail_quote", job_id=job_id)
        else:
            await run_retail_quote_pipeline(job_id=job_id)
        return {
            "status": "success",
            "message": "Measurement report uploaded and retail quote generation enqueued.",
            "document_id": ev_doc_id
        }

    if sol_doc and sol_doc.get("storage_path"):
        sol_path = sol_doc["storage_path"]
        sol_sha256 = sol_doc.get("sha256_hash") or ""
        sol_doc_id = sol_doc.get("id") or ""
        if redis:
            await redis.enqueue_job(
                "process_supplement_event",
                job_id=job_id,
                ev_pdf_path=str(ev_path),
                sol_pdf_path=str(sol_path),
                ev_sha256=ev_hash,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_sha256,
                sol_doc_id=sol_doc_id,
                generate_pdf=True,
                role=role
            )
        else:
            await _run_supplement_pipeline(
                job_id=job_id,
                ev_pdf_path=str(ev_path),
                sol_pdf_path=str(sol_path),
                ev_sha256=ev_hash,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_sha256,
                sol_doc_id=sol_doc_id,
                generate_pdf=True,
                ctx={"role": role},
            )
        return {
            "status": "success",
            "message": "Measurement report uploaded and supplement generation enqueued.",
            "document_id": ev_doc_id
        }

    return {
        "status": "success",
        "message": "Measurement report uploaded. Waiting for Statement of Loss to run supplement pipeline.",
        "document_id": ev_doc_id
    }


@router.post("/jobs/{job_id}/statement-of-loss", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def upload_statement_of_loss(
    request: Request,
    job_id: str,
    file: UploadFile = File(...),
    role: str = Depends(get_current_role)
):
    """
    Upload a carrier Statement of Loss PDF independently.
    If a measurement report is already present, triggers supplement generation.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF.")

    job_dir = FIELD_DOCS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_name = file.filename or "statement_of_loss.pdf"
    sol_path = job_dir / safe_name

    try:
        sol_hash = await stream_upload_safely(
            file, sol_path, max_bytes=25 * 1024 * 1024, allowed_magic_bytes=[b"%PDF-"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("sol_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save Statement of Loss PDF")

    # Idempotency check
    existing_doc = await asyncio.to_thread(get_job_document_by_hash, job_id, sol_hash)
    if existing_doc:
        logger.warning("idempotent_sol_upload_prevented", job_id=job_id, filename=safe_name, sha256=sol_hash)
        sol_path.unlink(missing_ok=True)
        return {
            "status": "success",
            "message": "Duplicate file detected. Skipped pipeline.",
            "document_id": existing_doc.get("id"),
            "filename": safe_name
        }

    sol_doc_id = await asyncio.to_thread(
        insert_job_document, job_id, safe_name, "SOL_PDF", str(sol_path), sol_hash, "office_only", "STATEMENT_OF_LOSS", True
    )

    # Readiness check
    conn = get_connection()
    try:
        job_row = conn.execute("SELECT job_type FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job_type = job_row["job_type"] if job_row else None
        ev_row = conn.execute(
            """SELECT id, storage_path, sha256_hash FROM job_documents 
               WHERE job_id = ? AND category IN (
                   'MEASUREMENT_REPORT', 'EAGLEVIEW', 'EAGLEVIEW_REPORT',
                   'HOVER_REPORT', 'HOVER_PDF', 'EAGLEVIEW_PDF'
               ) AND deleted_at IS NULL""",
            (job_id,)
        ).fetchone()
        ev_doc = dict(ev_row) if ev_row else None
    finally:
        conn.close()

    from app.core.utils import is_retail_job
    if is_retail_job(job_type):
        return {
            "status": "success",
            "message": "Statement of Loss uploaded.",
            "document_id": sol_doc_id
        }

    redis = getattr(request.app.state, "redis_pool", None)
    if ev_doc and ev_doc.get("storage_path"):
        ev_path = ev_doc["storage_path"]
        ev_sha256 = ev_doc.get("sha256_hash") or ""
        ev_doc_id = ev_doc.get("id") or ""
        if redis:
            await redis.enqueue_job(
                "process_supplement_event",
                job_id=job_id,
                ev_pdf_path=str(ev_path),
                sol_pdf_path=str(sol_path),
                ev_sha256=ev_sha256,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_hash,
                sol_doc_id=sol_doc_id,
                generate_pdf=True,
                role=role
            )
        else:
            await _run_supplement_pipeline(
                job_id=job_id,
                ev_pdf_path=str(ev_path),
                sol_pdf_path=str(sol_path),
                ev_sha256=ev_sha256,
                ev_doc_id=ev_doc_id,
                sol_sha256=sol_hash,
                sol_doc_id=sol_doc_id,
                generate_pdf=True,
                ctx={"role": role},
            )
        return {
            "status": "success",
            "message": "Statement of Loss uploaded and supplement generation enqueued.",
            "document_id": sol_doc_id
        }

    return {
        "status": "success",
        "message": "Statement of Loss uploaded. Waiting for measurement report to run supplement pipeline.",
        "document_id": sol_doc_id
    }


@router.post("/jobs/{job_id}/supplement_docs", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def upload_supplement_docs(
    request: Request,
    job_id: str, 
    ev_file: UploadFile = File(...), 
    sol_file: UploadFile = File(...),
    role: str = Depends(get_current_role)
):
    """
    [DEPRECATED] Upload both EagleView and Statement of Loss PDFs simultaneously to trigger the Supplement pipeline.
    Deprecated in v2.7.1 in favor of independent /measurement-report and /statement-of-loss endpoints.
    Internally delegates sequentially to upload_measurement_report and upload_statement_of_loss.
    """
    meas_result = await upload_measurement_report(request=request, job_id=job_id, file=ev_file, role=role)
    sol_result = await upload_statement_of_loss(request=request, job_id=job_id, file=sol_file, role=role)

    if sol_result.get("message") == "Statement of Loss uploaded.":
        msg = "Documents uploaded (retail job; supplement generation skipped)."
    elif "supplement generation enqueued" in sol_result.get("message", ""):
        msg = "Supplement generation enqueued (via legacy wrapper)."
    else:
        msg = f"Documents uploaded (via legacy wrapper): {sol_result.get('message', '')}"

    return {
        "status": "success",
        "message": msg,
        "measurement": meas_result,
        "sol": sol_result
    }


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
    request: Request,
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

    from app.services.field_access import assert_field_rep_owns_job
    if role == "field":
        assert_field_rep_owns_job(claims, job_id, request.method)

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
    if not actual_type or actual_type not in valid_types:
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

