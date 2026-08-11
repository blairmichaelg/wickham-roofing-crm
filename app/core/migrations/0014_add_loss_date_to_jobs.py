"""
Migration 0014: Add loss_date to jobs table.

Adds loss_date column to jobs table to synchronize and ensure consistency
between jobs and storm_verifications.
"""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0014_add_loss_date_to_jobs")


def up(conn: sqlite3.Connection) -> None:
    """Apply migration 0014."""
    logger.info("applying_migration", version=14, name="add_loss_date_to_jobs")

    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN loss_date TEXT")
        logger.info("column_added", column="loss_date")
    except sqlite3.OperationalError:
        logger.info("column_already_exists", column="loss_date")

    logger.info("migration_0014_complete")
