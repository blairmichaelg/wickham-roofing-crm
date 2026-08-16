"""Migration 0017: Add county and report_time_utc to storm_events."""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0017_add_county_and_report_time_to_storm_events")

def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=17, name="add_county_and_report_time_to_storm_events")
    cursor = conn.execute("PRAGMA table_info(storm_events)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    
    if "county" not in existing_cols:
        conn.execute("ALTER TABLE storm_events ADD COLUMN county TEXT")
        logger.info("column_added", col="county")
    if "report_time_utc" not in existing_cols:
        conn.execute("ALTER TABLE storm_events ADD COLUMN report_time_utc TIMESTAMP")
        logger.info("column_added", col="report_time_utc")
