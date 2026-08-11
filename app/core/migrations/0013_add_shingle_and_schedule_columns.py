"""
Migration 0013: Add shingle material fields and completed-jobs indexing.

Adds shingle_color and shingle_type columns to the jobs table to support
manual entry and AI/regex extraction from Statement of Loss and measurement
reports (EagleView / Hover). Also adds a convenience index on jobs(status)
for the completed-jobs archive lookup.

Shingle info is OPTIONAL and nullable. Existing rows are unaffected.
"""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0013_add_shingle_columns")


def up(conn: sqlite3.Connection) -> None:
    """Apply migration 0013."""
    logger.info("applying_migration", version=13,
                name="add_shingle_and_schedule_columns")

    # Add shingle columns (idempotent — IF NOT EXISTS guard not supported
    # in SQLite ALTER TABLE ADD COLUMN, so we use try/except)
    for col in [
        "shingle_color TEXT",
        "shingle_type TEXT",
    ]:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            logger.info("column_added", column=col)
        except sqlite3.OperationalError:
            logger.info("column_already_exists", column=col)

    # Index on jobs(status) to accelerate the completed-jobs archive
    # query that filters CLOSED / PAYMENT_RECEIVED rows.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status "
        "ON jobs(status)"
    )

    logger.info("migration_0013_complete")
