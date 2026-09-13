"""
Composite router for Field UX endpoints.
"""

from fastapi import APIRouter, Depends

from app.api.auth import verify_field
from app.api.field.actions import router as actions_router
from app.api.field.documents import router as documents_router
from app.api.field.evidence import router as evidence_router
from app.api.field.leads import router as leads_router
from app.api.field.photos import router as photos_router
from app.api.field.radar import router as radar_router
from app.api.field.sales_tools import router as sales_tools_router
from app.api.field.signatures import router as signatures_router

router = APIRouter(
    prefix="/api/field",
    tags=["field_ux"],
    dependencies=[Depends(verify_field)],
)

router.include_router(leads_router)
router.include_router(photos_router)
router.include_router(signatures_router)
router.include_router(documents_router)
router.include_router(sales_tools_router)
router.include_router(radar_router)
router.include_router(actions_router)
router.include_router(evidence_router)
