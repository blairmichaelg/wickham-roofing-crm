"""
Migration 0012: Add latitude and longitude columns to storm_events.

Allows storing exact coordinates for ingested NOAA storm events to compute proximity to jobs.
"""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0012_add_latitude_longitude_to_storm_events")

def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=12, name="add_latitude_longitude_to_storm_events")
    for col in [
        "latitude REAL",
        "longitude REAL",
    ]:
        try:
            conn.execute(f"ALTER TABLE storm_events ADD COLUMN {col}")
            logger.info("column_added", col=col)
        except sqlite3.OperationalError:
            logger.info("column_already_exists", col=col)
