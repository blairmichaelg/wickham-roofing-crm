"""
app/api/office/evidence.py — Office endpoints for Evidence Matrix and Supplement Packet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.auth import verify_office_user
from app.config import FIELD_DOCS_DIR
from app.core.database import get_db_connection
from app.services.evidence_matrix import (
    create_evidence_exhibit,
    get_job_evidence_exhibits,
    reorder_evidence_exhibits,
    update_evidence_exhibit,
)
from app.services.pdf.evidence_packet import evidence_packet_generator
from app.services.security import sanitize_download_filename

router = APIRouter(dependencies=[Depends(verify_office_user)])


class OfficeEvidenceCreatePayload(BaseModel):
    category: str = Field(..., description="decking, flashing, membrane_shingle_damage, ventilation, ice_and_water, interior, debris_access, other")
    observation: str = Field(..., min_length=3)
    location_roof_area: str | None = None
    document_id: str | None = None
    photo_path: str | None = None
    proposed_claim_line_ref: str | None = None
    status: str = Field("reviewed", description="draft, reviewed, included, excluded")


class OfficeEvidenceUpdatePayload(BaseModel):
    category: str | None = None
    observation: str | None = None
    location_roof_area: str | None = None
    proposed_claim_line_ref: str | None = None
    status: str | None = Field(None, description="draft, reviewed, included, excluded")
    requires_office_review: bool | None = None


class OfficeEvidenceReorderPayload(BaseModel):
    exhibit_ids: list[str] = Field(..., min_length=1)


class GeneratePacketPayload(BaseModel):
    version: int = Field(1, ge=1)
    included_only: bool = Field(False, description="If True, only exhibits with status='included' will be printed")


@router.get("/jobs/{job_id}/evidence")
async def get_office_evidence(job_id: str) -> dict[str, Any]:
    """
    Retrieve all evidence exhibits for a job.
    """
    exhibits = await asyncio.to_thread(get_job_evidence_exhibits, job_id=job_id)
    return {
        "status": "success",
        "job_id": job_id,
        "count": len(exhibits),
        "exhibits": exhibits,
    }


@router.post("/jobs/{job_id}/evidence")
async def create_office_evidence(job_id: str, payload: OfficeEvidenceCreatePayload) -> dict[str, Any]:
    """
    Create a new evidence exhibit from the office dashboard.
    """
    exhibit = await asyncio.to_thread(
        create_evidence_exhibit,
        job_id=job_id,
        category=payload.category,
        observation=payload.observation,
        location_roof_area=payload.location_roof_area,
        document_id=payload.document_id,
        photo_path=payload.photo_path,
        proposed_claim_line_ref=payload.proposed_claim_line_ref,
        author_rep_id="office_reviewer",
        source_type="office_entry",
        status=payload.status,
    )
    return {
        "status": "success",
        "exhibit": exhibit,
    }


@router.patch("/jobs/{job_id}/evidence/{exhibit_id}")
async def update_office_evidence(
    job_id: str,
    exhibit_id: str,
    payload: OfficeEvidenceUpdatePayload,
) -> dict[str, Any]:
    """
    Update an exhibit's review status, notes, or categorization.
    """
    updated = await asyncio.to_thread(
        update_evidence_exhibit,
        exhibit_id=exhibit_id,
        category=payload.category,
        observation=payload.observation,
        location_roof_area=payload.location_roof_area,
        proposed_claim_line_ref=payload.proposed_claim_line_ref,
        status=payload.status,
        requires_office_review=payload.requires_office_review,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Evidence exhibit not found or no valid changes provided")
    return {
        "status": "success",
        "exhibit": updated,
    }


@router.post("/jobs/{job_id}/evidence/reorder")
async def reorder_office_evidence(
    job_id: str,
    payload: OfficeEvidenceReorderPayload,
) -> dict[str, Any]:
    """
    Reorder exhibits sequence for packet generation.
    """
    await asyncio.to_thread(
        reorder_evidence_exhibits,
        job_id=job_id,
        exhibit_ids_in_order=payload.exhibit_ids,
    )
    return {
        "status": "success",
        "job_id": job_id,
        "reordered_count": len(payload.exhibit_ids),
    }


@router.post("/jobs/{job_id}/generate-evidence-packet")
async def generate_packet_endpoint(
    job_id: str,
    payload: GeneratePacketPayload | None = None,
) -> dict[str, Any]:
    """
    Generate a versioned Supplement Evidence Packet PDF.
    """
    version = payload.version if payload else 1
    included_only = payload.included_only if payload else False

    file_path = await asyncio.to_thread(
        evidence_packet_generator.generate_packet_sync,
        job_id=job_id,
        version=version,
        included_only=included_only,
    )
    return {
        "status": "success",
        "job_id": job_id,
        "version": version,
        "file_path": file_path,
        "filename": f"evidence_packet_v{version}.pdf",
    }


@router.get("/jobs/{job_id}/download-evidence-packet")
async def download_packet_endpoint(
    job_id: str,
    version: int = 1,
) -> FileResponse:
    """
    Download the generated evidence packet PDF.
    """
    target_path = Path(FIELD_DOCS_DIR) / job_id / f"evidence_packet_v{version}.pdf"
    if not target_path.exists():
        # Try finding any evidence packet for this job
        job_dir = Path(FIELD_DOCS_DIR) / job_id
        matching = list(job_dir.glob("evidence_packet_v*.pdf")) if job_dir.exists() else []
        if matching:
            target_path = sorted(matching)[-1]
        else:
            raise HTTPException(status_code=404, detail="No evidence packet generated for this job yet")

    filename = sanitize_download_filename(target_path.name)
    return FileResponse(
        str(target_path),
        media_type="application/pdf",
        filename=filename,
    )
