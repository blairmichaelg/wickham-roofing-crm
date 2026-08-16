import sys
import json
import math
import uuid
import sqlite3
import httpx
import structlog
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path for local runs/tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import get_settings
from app.services.storm_feed import NWSLiveStormFeed, haversine
from app.core.database import get_connection

logger = structlog.get_logger("app.workers.storm_worker")

async def ingest_storm_events(ctx: dict) -> None:
    """
    Ingest storm reports from NOAA, persist them in SQLite, and broadcast alerts.
    """
    logger.info("ingest_storm_events_started")
    settings = get_settings()
    feed = NWSLiveStormFeed()
    
    # Fetch recent storm reports
    reports = await feed.fetch_recent_reports()
    if not reports:
        logger.info("ingest_storm_events_no_reports_fetched")
        return
        
    # Load zipcodes for coordinates-to-zipcode resolution
    zip_path = Path("data/zipcodes.json")
    if not zip_path.exists():
        logger.error("missing_zipcodes_file_for_ingest")
        return
        
    with open(zip_path, 'r', encoding='utf-8') as f:
        zipcodes = json.load(f)
        
    conn = get_connection()
    
    inserted_count = 0
    new_alerts = []
    
    try:
        for r in reports:
            lat = r["latitude"]
            lon = r["longitude"]
            
            # 1. Ingestion boundary check (50 miles of office center)
            dist = haversine(lat, lon, settings.storm_office_lat, settings.storm_office_lon)
            if dist > settings.storm_ingest_radius_miles:
                continue
                
            # 2. Resolve closest zipcode for backward compatibility
            closest_zip = "Unknown"
            min_zip_dist = float('inf')
            for zc, coords in zipcodes.items():
                d = haversine(lat, lon, coords["lat"], coords["lon"])
                if d < min_zip_dist:
                    min_zip_dist = d
                    closest_zip = zc
                    
            # 3. Deduplication using primary key 'id'
            cur = conn.execute("SELECT 1 FROM storm_events WHERE id = ?", (r["id"],))
            if cur.fetchone():
                continue
                
            # 4. Insert into SQLite db
            conn.execute('''
                INSERT INTO storm_events (
                    id, zipcode, event_type, event_date, hail_size_inches, 
                    wind_speed_mph, source, latitude, longitude, county, report_time_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                r["id"],
                closest_zip,
                r["event_type"],
                r["report_time_utc"][:10], # YYYY-MM-DD
                r["hail_size_inches"],
                r["wind_speed_mph"],
                f"NOAA_LSR_{r['id']}",
                lat,
                lon,
                r["county"],
                r["report_time_utc"]
            ))
            inserted_count += 1
            
            # 5. Alert boundary & severity criteria check
            # - inside 30 miles
            # - Hail >= 1.0" OR Wind >= 58 mph OR Tornado
            is_alert = False
            alert_dist = haversine(lat, lon, settings.storm_office_lat, settings.storm_office_lon)
            if alert_dist <= settings.storm_alert_radius_miles:
                if r["event_type"] == "TORNADO":
                    is_alert = True
                elif r["event_type"] == "HAIL" and r["hail_size_inches"] >= settings.storm_alert_min_hail_inches:
                    is_alert = True
                elif r["event_type"] == "WIND" and r["wind_speed_mph"] >= settings.storm_alert_min_wind_mph:
                    is_alert = True
                    
            if is_alert:
                alert_payload = {
                    "type": "storm_alert",
                    "event_type": r["event_type"],
                    "hail_size_inches": r["hail_size_inches"],
                    "wind_speed_mph": r["wind_speed_mph"],
                    "latitude": lat,
                    "longitude": lon,
                    "county": r["county"],
                    "report_time_utc": r["report_time_utc"],
                    "loc_desc": r["loc_desc"],
                    "remarks": r["remarks"]
                }
                new_alerts.append(alert_payload)
                
        conn.commit()
        logger.info("ingest_storm_events_success", processed=len(reports), inserted=inserted_count, alerts_count=len(new_alerts))
    except Exception as e:
        conn.rollback()
        logger.error("ingest_storm_events_db_failed", error=str(e))
    finally:
        conn.close()
        
    # Publish triggered alerts to Redis Pub/Sub channel
    if new_alerts:
        redis_pool = ctx.get("redis")
        redis_to_close = None
        if not redis_pool:
            from app.infra import create_redis_pool
            redis_pool = await create_redis_pool()
            redis_to_close = redis_pool
            
        try:
            for alert in new_alerts:
                logger.info("publishing_storm_alert_to_redis", alert_id=alert.get("id"))
                await redis_pool.publish("channel:storm_alerts", json.dumps(alert))
        finally:
            if redis_to_close:
                await redis_to_close.close()
