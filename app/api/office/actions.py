"""
app/api/office/actions.py — Office Next Best Action & Triage endpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.api.auth import verify_office_user
from app.services.next_best_action import get_office_action_triage

router = APIRouter(dependencies=[Depends(verify_office_user)])


@router.get("/actions/triage")
async def get_office_triage() -> dict[str, Any]:
    """
    Return grouped office triage items:
    - stalled_jobs
    - unassigned_storm_opportunities
    - supplement_packets_awaiting_review
    - missing_production_artifacts
    """
    triage_data = await asyncio.to_thread(get_office_action_triage)
    return {
        "status": "success",
        "triage": triage_data,
    }
