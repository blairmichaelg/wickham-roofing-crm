"""
Admin Field Rep Management API — Phase 9.

Provides CRUD endpoints for managing field rep identities.
All endpoints are admin-only.
"""

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.auth import verify_admin
from app.core.database import (
    create_field_rep,
    list_field_reps,
    update_field_rep,
)

logger = structlog.get_logger("app.api.admin_reps")
router = APIRouter(
    prefix="/api/admin/reps",
    tags=["admin-reps"],
    dependencies=[Depends(verify_admin)]
)


@router.get("/", response_class=JSONResponse)
def get_reps(
    include_inactive: bool = False,
    _=Depends(verify_admin),
):
    """List all field reps. Inactive hidden by default."""
    return list_field_reps(include_inactive=include_inactive)


@router.post("/", response_class=JSONResponse, status_code=201)
def add_rep(
    payload: dict = Body(...),
    _=Depends(verify_admin),
):
    """
    Create a new field rep.
    Body: {"name": "Mike B.", "pin": "5432"}
    """
    name = payload.get("name", "").strip()
    pin = str(payload.get("pin", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required.")
    try:
        rep = create_field_rep(name=name, pin=pin)
        logger.info("rep_created_via_api", name=name)
        return rep
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{rep_id}", response_class=JSONResponse)
def edit_rep(
    rep_id: str,
    payload: dict = Body(...),
    _=Depends(verify_admin),
):
    """
    Update name, PIN, or active status.
    Body (all optional):
      {"name": "...", "pin": "...", "is_active": true/false}
    """
    name = payload.get("name")
    pin = str(payload.get("pin")) if payload.get("pin") else None
    is_active = payload.get("is_active")
    try:
        rep = update_field_rep(
            rep_id=rep_id,
            name=name,
            pin=pin,
            is_active=is_active,
        )
        logger.info("rep_updated_via_api", rep_id=rep_id)
        return rep
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{rep_id}/deactivate", response_class=JSONResponse)
def deactivate_rep(
    rep_id: str,
    _=Depends(verify_admin),
):
    """
    Soft-delete a rep. Their historical commission data is preserved.
    They can no longer log in.
    """
    try:
        rep = update_field_rep(rep_id=rep_id, is_active=False)
        logger.info("rep_deactivated", rep_id=rep_id)
        return {"status": "deactivated", **rep}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
