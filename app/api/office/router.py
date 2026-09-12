"""
Unified Office API router mounting decomposed domain routers.
"""

from fastapi import APIRouter

from app.api.office.billing import router as billing_router
from app.api.office.contracts_downloads import router as contracts_downloads_router
from app.api.office.contracts_supplement_pipeline import router as contracts_supplement_pipeline_router
from app.api.office.contracts_uploads import router as contracts_uploads_router
from app.api.office.jobs import router as jobs_router
from app.api.office.scheduling import router as scheduling_router

router = APIRouter(prefix="/api/office", tags=["office_ux"])

router.include_router(jobs_router)
router.include_router(billing_router)
router.include_router(scheduling_router)
router.include_router(contracts_uploads_router)
router.include_router(contracts_downloads_router)
router.include_router(contracts_supplement_pipeline_router)
