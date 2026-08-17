"""Migration 0019: Normalize existing NWS location shorthand in county field."""
import sqlite3

import structlog

from app.services.storm_feed import normalize_nws_location

logger = structlog.get_logger("app.core.migrations.0019_normalize_nws_locations")

def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=19, name="normalize_nws_locations")
    
    cursor = conn.execute("SELECT id, county FROM storm_events WHERE county IS NOT NULL AND county != ''")
    rows = cursor.fetchall()
    
    updated_count = 0
    for row in rows:
        try:
            row_id = row["id"]
            county = row["county"]
        except (TypeError, IndexError, KeyError):
            row_id = row[0]
            county = row[1]
            
        normalized = normalize_nws_location(county)
        if normalized != county:
            conn.execute("UPDATE storm_events SET county = ? WHERE id = ?", (normalized, row_id))
            updated_count += 1
            
    logger.info("migration_completed", version=19, name="normalize_nws_locations", updated_rows_count=updated_count)
