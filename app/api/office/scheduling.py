"""
Production scheduling, material orders, and operations brief for Office Control Center.
"""

import asyncio
import json
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import verify_admin, verify_office_role
from app.core.database import (
    JobStatus,
    get_connection,
    insert_material_order,
    insert_schedule,
    update_job_status,
)
from app.core.utils import now_utc
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger("app.api.office.scheduling")
router = APIRouter()

class ProductionPayload(BaseModel):
    """ProductionPayload definition."""
    supplier_name: str
    delivery_date: str
    crew_name: str
    install_date: str


class ManualFlashingPayload(BaseModel):
    """ManualFlashingPayload definition."""
    flashing_lf: float
    step_flashing_lf: float


class MaterialOrderPayload(BaseModel):
    """MaterialOrderPayload definition."""
    supplier_name: str
    delivery_date: str


def _sync_update_job_production(job_id: str, payload: ProductionPayload):
    # Dummy BOM JSON for now, in a real scenario we'd pull the actual calculated BOM
    dummy_bom = json.dumps({"status": "scheduled_for_delivery"})
    
    insert_material_order(
        job_id=job_id,
        supplier_name=payload.supplier_name,
        delivery_date=payload.delivery_date,
        bom_json=dummy_bom
    )
    
    insert_schedule(
        job_id=job_id,
        crew_name=payload.crew_name,
        install_date=payload.install_date,
        delivery_date=payload.delivery_date,
        status="SCHEDULED"
    )
    
    update_job_status(
        job_id,
        JobStatus.MATERIAL_ORDERED,
        f"Material order placed with {payload.supplier_name}, delivery {payload.delivery_date}"
    )


@router.post("/jobs/{job_id}/production", dependencies=[Depends(verify_admin)])
async def update_job_production(job_id: str, payload: ProductionPayload, bg_tasks: BackgroundTasks):
    """
    Unified route to set both material orders and installation schedule.
    Transitions job to MATERIAL_ORDERED. Operations must confirm MATERIALS_ON_SITE before INSTALL_SCHEDULED becomes valid.
    """
    try:
        await asyncio.to_thread(_sync_update_job_production, job_id, payload)
        
        bg_tasks.add_task(backup_database)
        
        return {"status": "success", "message": "Production scheduled."}
    except Exception as e:
        logger.error("production_update_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to schedule production.")


@router.post("/jobs/{job_id}/material_order", dependencies=[Depends(verify_admin), Depends(check_rate_limit)])
async def generate_material_order(job_id: str, payload: MaterialOrderPayload, bg_tasks: BackgroundTasks):
    """
    Triggers the generation of the supplier PO and updates job status to MATERIAL_ORDERED.
    """
    try:
        from app.core.pipeline import generate_material_order_pipeline
        await generate_material_order_pipeline(job_id, payload.supplier_name, payload.delivery_date)
        bg_tasks.add_task(backup_database)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("material_order_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process material order")


@router.post("/jobs/{job_id}/manual_flashing", dependencies=[Depends(verify_admin)])
def manual_flashing(job_id: str, payload: ManualFlashingPayload):
    """Saves manual flashing entry and makes job eligible for pipeline retry."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE jobs SET flashing_lf = ?, step_flashing_lf = ? WHERE id = ?",
            (payload.flashing_lf, payload.step_flashing_lf, job_id)
        )
        if conn.total_changes == 0:
            raise HTTPException(status_code=404, detail="Job not found")
            
        # Re-triggering the pipeline implies the job should move back to a state that allows it.
        # But per requirements: "After persisting, the job should be eligible for pipeline re-trigger."
        conn.execute("COMMIT")
        return {"status": "success", "message": "Manual flashing entry saved."}
    except HTTPException:
        conn.execute("ROLLBACK")
        raise
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("manual_flashing_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save manual flashing.")
    finally:
        conn.close()


class MaterialRow(BaseModel):
    """MaterialRow definition."""
    job_id: str
    homeowner_name: str
    supplier_name: str
    delivery_date: str
    materials_ordered: int
    materials_on_site: int
    status: str


class OperationsBrief(BaseModel):
    """OperationsBrief definition."""
    deliveries_today: int
    crews_today: int
    material_rows: list[MaterialRow]


@router.get("/operations/brief", response_model=OperationsBrief, dependencies=[Depends(verify_admin)])
def get_operations_brief():
    """Zero-click read projection for operations dashboard."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT m.job_id, j.homeowner_name, m.supplier_name, m.delivery_date,
                   j.materials_ordered, j.materials_on_site, j.status AS job_status
            FROM material_orders m
            JOIN jobs j ON m.job_id = j.id
        """)
        m_rows = cursor.fetchall()
        
        material_rows = []
        deliveries_today = 0
        today_str = now_utc().strftime("%Y-%m-%d")
        
        for r in m_rows:
            d_date = r["delivery_date"]
            if d_date == today_str:
                deliveries_today += 1
            material_rows.append(MaterialRow(
                job_id=r["job_id"],
                homeowner_name=r["homeowner_name"],
                supplier_name=r["supplier_name"],
                delivery_date=d_date,
                materials_ordered=r["materials_ordered"],
                materials_on_site=r["materials_on_site"],
                status=r["job_status"]
            ))
            
        cursor = conn.execute("SELECT COUNT(*) as crews FROM schedule WHERE install_date LIKE ?", (f"{today_str}%",))
        c_row = cursor.fetchone()
        crews_today = c_row["crews"] if c_row else 0
        
        return OperationsBrief(
            deliveries_today=deliveries_today,
            crews_today=crews_today,
            material_rows=material_rows
        )
    finally:
        conn.close()


@router.get("/storms/targets", dependencies=[Depends(verify_office_role)])
async def get_storm_canvassing_targets(
    window_hours: int = 72,
    limit: int = 10,
    enrich: bool = False,
):
    """
    Return the top-N canvassing target areas ranked by storm severity.

    Query params:
      - window_hours (int, default 72): look-back window in hours.
      - limit (int, default 10): maximum number of target areas returned.
      - enrich (bool, default False): optionally enrich targets with Census demographic and OSM footprint data.

    Returns a list of dicts with location, severity, hail/wind stats, and event count.
    Accessible to all authenticated users (field reps and office staff).
    """
    from app.config import get_settings
    from app.services.canvassing_targets import (
        get_enriched_canvassing_targets,
        get_ranked_canvassing_targets,
    )
    
    settings = get_settings()
    conn = get_connection()
    try:
        row = conn.execute("SELECT MAX(ingested_at) FROM storm_events").fetchone()
        last_refreshed = row[0] if (row and row[0]) else None
    finally:
        conn.close()

    try:
        if enrich:
            targets = await get_enriched_canvassing_targets(
                window_hours=window_hours,
                limit=limit,
            )
        else:
            targets = await asyncio.to_thread(
                get_ranked_canvassing_targets,
                window_hours=window_hours,
                limit=limit,
            )
        return {
            "targets": targets,
            "window_hours": window_hours,
            "count": len(targets),
            "min_hail": settings.storm_alert_min_hail_inches,
            "min_hail_inches": settings.storm_alert_min_hail_inches,
            "min_wind": settings.storm_alert_min_wind_mph,
            "min_wind_mph": settings.storm_alert_min_wind_mph,
            "last_refreshed_utc": last_refreshed,
        }
    except Exception as exc:
        logger.error("storm_targets_fetch_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch storm canvassing targets.")

