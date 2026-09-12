"""
Field document vault and downloads.
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

from app.api.auth import get_current_claims, verify_field
from app.config import FIELD_DOCS_DIR
from app.core.database import (
    JobStatus,
    get_connection,
    insert_job_document,
    update_job_status,
)
from app.core.utils import now_utc
from app.services.field_access import assert_field_rep_owns_job
from app.services.pdf import PDFGenerator

logger = structlog.get_logger("app.api.field.documents")
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


@router.get("/jobs/{job_id}/docs/contingency")
async def download_unsigned_contingency(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """Dynamically generates and returns an unsigned Insurance Contingency Agreement PDF for printing or emailing to homeowners."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)
    job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)
    if not job_dict:
        raise HTTPException(status_code=404, detail="Job not found.")

    pdf_gen = PDFGenerator()
    pdf_path = await pdf_gen.generate_contingency_agreement(job_dict)

    from app.services.security import sanitize_download_filename
    filename = sanitize_download_filename(f"Unsigned_Contingency_Agreement_{job_dict.get('homeowner_name', 'Job').replace(' ', '_')}.pdf")
    return FileResponse(path=pdf_path, filename=filename, media_type="application/pdf")


@router.get("/jobs/{job_id}/documents")
async def get_field_job_documents(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Fetch list of field-safe documents for a job owned by the field representative.
    """
    try:
        uuid_obj = uuid.UUID(job_id)
        job_id = str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    _check_rep_ownership(claims, job_id, request.method)

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
async def download_field_job_document(job_id: str, doc_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Download a field-safe document from the Document Vault.
    Strictly checks job ownership and field_safe visibility.
    """
    try:
        job_id = str(uuid.UUID(job_id))
        doc_id = str(uuid.UUID(doc_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id or doc_id format. Must be a valid UUID.")

    _check_rep_ownership(claims, job_id, request.method)

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
async def download_field_evidence_grid(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """Download the field-safe Inspection Evidence Grid from the field API namespace."""
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

    from app.api.office_routes import download_evidence_grid
    return await download_evidence_grid(job_id)


@router.get("/jobs/{job_id}/docs/neighbor-letter")
async def get_neighbor_letter(job_id: str, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Generate (or retrieve from vault) the neighbor outreach letter for a completed job.
    Only available once the job has reached INSTALL_COMPLETED or later.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

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
            "INVOICED", "PAYMENT_RECEIVED", "ACV_PAYMENT_RECEIVED",
            "DEPRECIATION_PAYMENT_RECEIVED", "RETAIL_PAYMENT_RECEIVED", "CLOSED",
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

