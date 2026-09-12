"""
Field storm radar and local storm targeting endpoints.
"""

import asyncio
import json
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import get_current_claims, verify_field
from app.core.database import get_connection
from app.core.utils import normalize_zip, now_utc, now_utc_iso

logger = structlog.get_logger("app.api.field.radar")
router = APIRouter()

@router.get("/storms/targets")
async def get_field_storm_targets(
    limit: int = 15,
    role: str = Depends(verify_field)
):
    """
    Fetch prioritized storm canvassing target areas for field reps.
    Exposes only physical weather parameters and canvassing priority metrics (no financials).
    """
    from app.config import get_settings
    from app.core.database import get_storm_target_summaries

    settings = get_settings()
    raw_targets = await asyncio.to_thread(
        get_storm_target_summaries,
        window_hours=settings.storm_canvassing_window_hours,
        limit=limit,
        radius_miles=settings.storm_canvassing_radius_miles,
    )
    sanitized = []
    for t in raw_targets:
        zip_val = t.get("zipcode", "")
        sanitized.append({
            "zip": zip_val,
            "zipcode": zip_val,
            "location": t.get("location", "Unknown"),
            "event_count": t.get("event_count", 0),
            "hail_events": t.get("hail_events", 0),
            "max_hail": t.get("max_hail_inches", 0.0),
            "max_hail_inches": t.get("max_hail_inches", 0.0),
            "wind_events": t.get("wind_events", 0),
            "max_wind": t.get("max_wind_mph", 0.0),
            "max_wind_mph": t.get("max_wind_mph", 0.0),
            "priority_label": t.get("priority_label", "Standard"),
            "reasons": t.get("priority_reason", ""),
            "priority_reason": t.get("priority_reason", ""),
            "has_tornado": t.get("has_tornado", False),
            "last_event_utc": t.get("latest_event_time_utc") or t.get("last_event_utc") or "",
            "latest_event_time_utc": t.get("latest_event_time_utc") or t.get("last_event_utc") or "",
        })
    from app.core.database import get_connection
    conn = get_connection()
    try:
        row = conn.execute("SELECT MAX(ingested_at) FROM storm_events").fetchone()
        last_refreshed = row[0] if (row and row[0]) else None
    finally:
        conn.close()

    return {
        "status": "success",
        "count": len(sanitized),
        "window_hours": settings.storm_canvassing_window_hours,
        "min_hail": settings.storm_alert_min_hail_inches,
        "min_hail_inches": settings.storm_alert_min_hail_inches,
        "min_wind": settings.storm_alert_min_wind_mph,
        "min_wind_mph": settings.storm_alert_min_wind_mph,
        "radius_miles": settings.storm_canvassing_radius_miles,
        "last_refreshed_utc": last_refreshed,
        "targets": sanitized,
    }


@router.get("/storms/{zipcode}")
async def get_zip_storms(
    zipcode: str,
    window_hours: int | None = None,
    min_hail: float | None = None,
    min_wind: float | None = None,
    role: str = Depends(verify_field)
):
    """Fetch recent storm events for a given zip code for field sales reps."""
    from datetime import timedelta

    from app.config import get_settings
    settings = get_settings()
    hours = window_hours if window_hours is not None else settings.storm_fresh_window_hours
    hail_threshold = min_hail if min_hail is not None else settings.storm_alert_min_hail_inches
    wind_threshold = min_wind if min_wind is not None else settings.storm_alert_min_wind_mph

    clean_zip = normalize_zip(zipcode)
    cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT event_date, event_type, MAX(hail_size_inches) as hail_size_inches, MAX(wind_speed_mph) as wind_speed_mph 
            FROM storm_events 
            WHERE zipcode = ?
              AND report_time_utc >= ?
              AND (
                (event_type = 'HAIL' AND hail_size_inches >= ?)
                OR
                (event_type = 'WIND' AND wind_speed_mph >= ?)
                OR
                (event_type = 'TORNADO')
              )
            GROUP BY event_date, event_type 
            ORDER BY event_date DESC 
            LIMIT 5
            """,
            (clean_zip, cutoff, hail_threshold, wind_threshold)
        )
        raw_events = [dict(r) for r in cursor.fetchall()]
        formatted_events = []
        for e in raw_events:
            raw_type = str(e.get("event_type", "")).upper()
            hail = e.get("hail_size_inches") or 0.0
            wind = e.get("wind_speed_mph") or 0.0

            if "HAIL" in raw_type or hail > 0:
                label = "Hail Event"
                metric = f"{hail:.2f}\" Hail" if hail > 0 else "Hail Verified"
                badge_class = "bg-amber-900/80 text-amber-300 border-amber-600"
            elif "GST" in raw_type:
                label = "Severe Wind Gust"
                metric = f"{wind:.0f} mph Gust" if wind > 0 else "Severe Gust"
                badge_class = "bg-blue-900/80 text-blue-300 border-blue-600"
            else:
                label = "Thunderstorm Wind Damage"
                metric = f"{wind:.0f} mph Wind" if wind > 0 else "Wind Damage"
                badge_class = "bg-red-900/80 text-red-300 border-red-600"

            e["display_label"] = label
            e["display_metric"] = metric
            e["badge_class"] = badge_class
            e["formatted_date"] = str(e.get("event_date", ""))[:10]
            formatted_events.append(e)

        cursor_ref = conn.execute("SELECT MAX(ingested_at) FROM storm_events")
        row_ref = cursor_ref.fetchone()
        last_refreshed = row_ref[0] if (row_ref and row_ref[0]) else now_utc_iso()

        talking_point = None
        if formatted_events:
            top = formatted_events[0]
            top_hail = top.get("hail_size_inches") or 0.0
            top_wind = top.get("wind_speed_mph") or 0.0
            top_date = top.get("formatted_date", "")
            if top_hail > 0:
                talking_point = f"This ZIP had {top_hail:.2f}\" hail on {top_date} — mention local damage."
            elif top_wind > 0:
                talking_point = f"This ZIP had {top_wind:.0f} mph wind on {top_date} — mention local roof and shingle uplift."
            else:
                talking_point = f"Severe storm reported on {top_date} — mention local insurance claim activity."

        return {
            "events": formatted_events,
            "talking_point": talking_point,
            "window_hours": hours,
            "min_hail_inches": hail_threshold,
            "min_wind_mph": wind_threshold,
            "last_refreshed_utc": last_refreshed,
        }
    finally:
        conn.close()

