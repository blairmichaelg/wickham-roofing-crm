"""Migration 0018: Add dedup_key, distance_miles_from_office, and ingested_at to storm_events."""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0018_add_storm_dedup_and_audit")

def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=18, name="add_storm_dedup_and_audit")
    cursor = conn.execute("PRAGMA table_info(storm_events)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    
    if "dedup_key" not in existing_cols:
        conn.execute("ALTER TABLE storm_events ADD COLUMN dedup_key TEXT")
        logger.info("column_added", col="dedup_key")
    if "distance_miles_from_office" not in existing_cols:
        conn.execute("ALTER TABLE storm_events ADD COLUMN distance_miles_from_office REAL")
        logger.info("column_added", col="distance_miles_from_office")
    if "ingested_at" not in existing_cols:
        conn.execute("ALTER TABLE storm_events ADD COLUMN ingested_at TEXT")
        logger.info("column_added", col="ingested_at")
        
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_storm_events_dedup_key ON storm_events(dedup_key)")
    logger.info("unique_index_created", name="idx_storm_events_dedup_key")
