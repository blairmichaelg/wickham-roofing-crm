"""
Upload endpoints for EagleView, Hover, measurement reports, and Statements of Loss.
Part of decomposed Office Control Center contracts module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from app.api.auth import (
    get_current_role,
    verify_admin,
)
from app.config import FIELD_DOCS_DIR
from app.core.database import (
    get_connection,
    get_job_document_by_hash,
    insert_job_document,
)
from app.core.pipeline import run_full_office_pipeline, run_supplement_pipeline
from app.core.upload_utils import stream_upload_safely
from app.services.hover_extractor import detect_pdf_format
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.office.contracts_uploads")
router = APIRouter()


def _detect_pdf_format(p: Path) -> str:
    import sys
    if "app.api.office.contracts" in sys.modules:
        mod = sys.modules["app.api.office.contracts"]
        if hasattr(mod, "detect_pdf_format"):
            from app.services.hover_extractor import detect_pdf_format as real_detect
            if mod.detect_pdf_format is not real_detect:
                return mod.detect_pdf_format(p)
    import unittest.mock
    if isinstance(detect_pdf_format, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return detect_pdf_format(p)
    return detect_pdf_format(p)


async def _stream_upload_safely(*args, **kwargs):
    import sys
    if "app.api.office.contracts" in sys.modules:
        mod = sys.modules["app.api.office.contracts"]
        if hasattr(mod, "stream_upload_safely"):
            from app.core.upload_utils import stream_upload_safely as real_stream
            if mod.stream_upload_safely is not real_stream:
                res = mod.stream_upload_safely(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    return await res
                return res
    import unittest.mock
    if isinstance(stream_upload_safely, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        res = stream_upload_safely(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res
    return await stream_upload_safely(*args, **kwargs)


def _get_job_document_by_hash(*args, **kwargs):
    import sys
    if "app.api.office.contracts" in sys.modules:
        mod = sys.modules["app.api.office.contracts"]
        if hasattr(mod, "get_job_document_by_hash"):
            from app.core.database import get_job_document_by_hash as real_doc
            if mod.get_job_document_by_hash is not real_doc:
                return mod.get_job_document_by_hash(*args, **kwargs)
    import unittest.mock
    if isinstance(get_job_document_by_hash, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return get_job_document_by_hash(*args, **kwargs)
    return get_job_document_by_hash(*args, **kwargs)


async def _run_full_office_pipeline(*args, **kwargs):
    import sys
    if "app.api.office.contracts" in sys.modules:
        mod = sys.modules["app.api.office.contracts"]
        if hasattr(mod, "run_full_office_pipeline"):
            from app.core.pipeline import run_full_office_pipeline as real_pipe
            if mod.run_full_office_pipeline is not real_pipe:
                res = mod.run_full_office_pipeline(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    return await res
                return res
    import unittest.mock
    if isinstance(run_full_office_pipeline, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        res = run_full_office_pipeline(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res
    return await run_full_office_pipeline(*args, **kwargs)


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


def _fetch_homeowner_name_sync(job_id: str) -> str:
    """Fetch the homeowner's name for a given job synchronously."""
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
        file_hash = await _stream_upload_safely(
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
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Check for duplicate hash
    existing_doc = await asyncio.to_thread(_get_job_document_by_hash, job_id, file_hash)
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
        ev_hash = await _stream_upload_safely(
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
    existing_doc = await asyncio.to_thread(_get_job_document_by_hash, job_id, ev_hash)
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
        sol_hash = await _stream_upload_safely(
            file, sol_path, max_bytes=25 * 1024 * 1024, allowed_magic_bytes=[b"%PDF-"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("sol_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save Statement of Loss PDF")

    # Idempotency check
    existing_doc = await asyncio.to_thread(_get_job_document_by_hash, job_id, sol_hash)
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
        file_hash = await _stream_upload_safely(
            file, 
            pdf_path,
            max_bytes=25 * 1024 * 1024,
            allowed_magic_bytes=[b"%PDF-", b"\xFF\xD8\xFF", b"\x89PNG\r\n\x1A\n"]
        )
        
        existing_doc = await asyncio.to_thread(_get_job_document_by_hash, job_id, file_hash)
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
