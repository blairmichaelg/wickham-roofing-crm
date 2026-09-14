"""Migration 0030: Add updated_at column and trigger to jobs table.

Enables optimistic concurrency control on office mutation endpoints
(commission override and manual geometry/measurements entry).
"""

from __future__ import annotations

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0030_add_jobs_updated_at")


def up(conn: sqlite3.Connection) -> None:
    """Add updated_at column to jobs table and backfill with created_at."""
    logger.info("applying_migration", version=30, name="add_jobs_updated_at")
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(jobs)")
    cols = [r[1] for r in cursor.fetchall()]
    if "updated_at" not in cols:
        cursor.execute("ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cursor.execute("UPDATE jobs SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL")
    
    # Create an UPDATE trigger on jobs table to automatically refresh updated_at on any write
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_jobs_updated_at
        AFTER UPDATE ON jobs
        FOR EACH ROW
        BEGIN
            UPDATE jobs SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id AND (NEW.updated_at IS OLD.updated_at);
        END;
    """)
    logger.info("migration_30_applied_successfully")
