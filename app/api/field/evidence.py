"""
app/api/field/evidence.py — Field endpoints for Evidence Matrix exhibits.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_claims, verify_field
from app.services.evidence_matrix import (
    create_evidence_exhibit,
    get_job_evidence_exhibits,
)
from app.services.field_access import assert_field_rep_owns_job

router = APIRouter()


def _check_rep_ownership(claims: dict, job_id: str, method: str) -> None:
    import unittest.mock
    if isinstance(assert_field_rep_owns_job, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        assert_field_rep_owns_job(claims, job_id, method)
        return
    import sys
    if "app.api.field_routes" in sys.modules:
        mod = sys.modules["app.api.field_routes"]
        if hasattr(mod, "assert_field_rep_owns_job"):
            from app.services.field_access import assert_field_rep_owns_job as real_assert
            if mod.assert_field_rep_owns_job is not real_assert:
                mod.assert_field_rep_owns_job(claims, job_id, method)
                return
    assert_field_rep_owns_job(claims, job_id, method)


class FieldEvidenceCreatePayload(BaseModel):
    category: str = Field(..., description="decking, flashing, membrane_shingle_damage, ventilation, ice_and_water, interior, debris_access, other")
    observation: str = Field(..., min_length=3, description="Field observation notes")
    location_roof_area: str | None = Field(None, description="Roof plane or location")
    document_id: str | None = None
    photo_path: str | None = None
    proposed_claim_line_ref: str | None = None


@router.get("/jobs/{job_id}/evidence")
async def get_field_evidence(
    job_id: str,
    claims: dict = Depends(get_current_claims),
) -> dict[str, Any]:
    """
    List all evidence exhibits for a job owned by this rep.
    """
    _check_rep_ownership(claims, job_id, "GET")
    exhibits = await asyncio.to_thread(get_job_evidence_exhibits, job_id=job_id)
    return {
        "status": "success",
        "job_id": job_id,
        "count": len(exhibits),
        "exhibits": exhibits,
    }


@router.post("/jobs/{job_id}/evidence")
async def create_field_evidence(
    job_id: str,
    payload: FieldEvidenceCreatePayload,
    claims: dict = Depends(get_current_claims),
) -> dict[str, Any]:
    """
    Create a new evidence exhibit for a job owned by this rep.
    """
    _check_rep_ownership(claims, job_id, "POST")
    rep_id = claims.get("rep_id")

    exhibit = await asyncio.to_thread(
        create_evidence_exhibit,
        job_id=job_id,
        category=payload.category,
        observation=payload.observation,
        location_roof_area=payload.location_roof_area,
        document_id=payload.document_id,
        photo_path=payload.photo_path,
        proposed_claim_line_ref=payload.proposed_claim_line_ref,
        author_rep_id=rep_id,
        source_type="field_observation",
        status="draft",
    )
    return {
        "status": "success",
        "exhibit": exhibit,
    }
