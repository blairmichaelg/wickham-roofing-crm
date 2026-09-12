"""
Backward-compatibility re-export shim for decomposed contracts module.

Decomposed in Task B into:
- app.api.office.contracts_uploads
- app.api.office.contracts_downloads
- app.api.office.contracts_supplement_pipeline
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.office.contracts_downloads import (
    EXPORT_DIR,
    download_cancellation,
    download_completion,
    download_contingency,
    download_evidence_grid,
    download_export,
    download_job_document,
    download_po,
    get_inspection_letter,
)
from app.api.office.contracts_downloads import (
    router as downloads_router,
)
from app.api.office.contracts_supplement_pipeline import (
    approve_supplement,
    deny_supplement,
    download_escalation,
    download_rebuttal,
    download_supplement_pdf_route,
    mark_supplement_sent_route,
    queue_escalation,
    trigger_supplement_route,
)
from app.api.office.contracts_supplement_pipeline import (
    router as supplement_pipeline_router,
)
from app.api.office.contracts_uploads import (
    _detect_pdf_format,
    _fetch_homeowner_name_sync,
    upload_eagleview,
    upload_job_document,
    upload_measurement_report,
    upload_statement_of_loss,
    upload_supplement_docs,
)

# Re-export routers
from app.api.office.contracts_uploads import (
    router as uploads_router,
)
from app.core.database import JobStatus, get_job_document_by_hash, update_job_status
from app.core.pipeline import run_full_office_pipeline, run_supplement_pipeline
from app.core.upload_utils import stream_upload_safely

# Re-export service symbols frequently patched in test suites
from app.services.hover_extractor import detect_pdf_format
from app.services.inspection_summary import get_inspection_summary
from app.services.pdf import PDFGenerator

# Unified composite router for backward-compatibility
router = APIRouter()
router.include_router(uploads_router)
router.include_router(downloads_router)
router.include_router(supplement_pipeline_router)

__all__ = [
    "router",
    "uploads_router",
    "downloads_router",
    "supplement_pipeline_router",
    # Upload endpoints
    "upload_eagleview",
    "upload_measurement_report",
    "upload_statement_of_loss",
    "upload_supplement_docs",
    "upload_job_document",
    "_detect_pdf_format",
    "_fetch_homeowner_name_sync",
    # Download endpoints
    "download_evidence_grid",
    "download_job_document",
    "download_export",
    "get_inspection_letter",
    "download_po",
    "download_cancellation",
    "download_completion",
    "download_contingency",
    "EXPORT_DIR",
    # Supplement endpoints
    "trigger_supplement_route",
    "mark_supplement_sent_route",
    "download_supplement_pdf_route",
    "approve_supplement",
    "deny_supplement",
    "download_rebuttal",
    "queue_escalation",
    "download_escalation",
    # Patched service dependencies
    "detect_pdf_format",
    "stream_upload_safely",
    "get_job_document_by_hash",
    "run_full_office_pipeline",
    "run_supplement_pipeline",
    "PDFGenerator",
    "get_inspection_summary",
    "update_job_status",
    "JobStatus",
]
