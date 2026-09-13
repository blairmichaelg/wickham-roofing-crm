"""
app/api/field/actions.py — Field Next Best Action endpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.api.auth import get_current_claims, verify_field
from app.services.next_best_action import get_field_best_actions

router = APIRouter()


@router.get("/actions/today")
async def get_today_best_actions(
    limit: int = 5,
    claims: dict = Depends(get_current_claims),
) -> dict[str, Any]:
    """
    Return top-ranked next best actions for the authenticated field rep.
    Filtered to rep-owned and unassigned jobs, sorted by deterministic priority.
    """
    rep_id = claims.get("rep_id")
    actions = await asyncio.to_thread(get_field_best_actions, rep_id=rep_id, limit=limit)
    return {
        "status": "success",
        "count": len(actions),
        "actions": actions,
    }
