"""
Pure inspection summary construction service.
Decoupled from HTTP routing and auth checking to prevent circular imports.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from app.core.cache import get_cached_analyses_for_job
from app.core.database import get_connection, insert_job_document
from app.core.inspection_models import InspectionJob, get_stable_photos

logger = structlog.get_logger("app.services.inspection_summary")

FIELD_PHOTOS_DIR = Path("field_photos")


def _get_photos_dir() -> Path:
    """Return the effective field photos directory, supporting test monkeypatching."""
    import sys
    if "app.api.field_routes" in sys.modules:
        fr = sys.modules["app.api.field_routes"]
        if hasattr(fr, "FIELD_PHOTOS_DIR"):
            return Path(fr.FIELD_PHOTOS_DIR)
    return FIELD_PHOTOS_DIR


async def get_inspection_summary(job_id: str, claims: dict[str, Any] | None = None) -> InspectionJob:
    """
    Retrieve the full InspectionJob summary.
    Constructs the job by scanning the local field_photos/{job_id} directory
    and reading available analyses directly from the SQLite cache.
    """
    job_dir = _get_photos_dir() / job_id

    # Get local photos if directory exists
    photos = []
    if job_dir.exists() and job_dir.is_dir():
        # Settle seconds = 0 for direct HTTP uploads (no Drive sync delay)
        photos = await asyncio.to_thread(get_stable_photos, job_dir, 0)

        # Ensure all photos are registered in the universal document vault for all roles
        if photos:
            def _sync_photos_to_vault() -> None:
                for p in photos:
                    try:
                        insert_job_document(
                            job_id,
                            p.filepath.name,
                            "image/jpeg",
                            str(p.filepath),
                            p.sha256,
                            "field_safe",
                            "INSPECTION_PHOTO",
                            False,
                        )
                    except Exception:
                        pass

            await asyncio.to_thread(_sync_photos_to_vault)

    # Retrieve all cached analyses for this job
    analyses = await asyncio.to_thread(get_cached_analyses_for_job, job_id)

    # Fetch real address and inspector from the jobs table
    property_address = "Unknown Address"
    inspector_name = "Wickham Roofing LLC"
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT address_line1, city, state, postal_code, inspector_name, canvasser_name FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if row:
            property_address = f"{row['address_line1']}, {row['city']}, {row['state']} {row['postal_code']}"
            if row["inspector_name"]:
                inspector_name = row["inspector_name"]
            elif row["canvasser_name"]:
                inspector_name = row["canvasser_name"]
    finally:
        conn.close()

    job = InspectionJob(
        job_id=job_id,
        property_address=property_address,
        inspection_date=datetime.now(),
        inspector_name=inspector_name,
        photos=photos,
        analyses=analyses,
    )

    logger.info(
        "inspection_summary_retrieved",
        job_id=job_id,
        photos_count=job.total_photos,
        analyses_count=len(job.analyses),
    )
    return job
