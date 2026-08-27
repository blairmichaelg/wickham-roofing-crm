"""Migration 0020: Add review/referral tracking fields to jobs and severity_score to storm_events."""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0020_add_sales_and_review_fields")


def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=20, name="add_sales_and_review_fields")

    # --- storm_events: add severity_score for canvassing prioritization ---
    cursor = conn.execute("PRAGMA table_info(storm_events)")
    storm_cols = {row[1] for row in cursor.fetchall()}

    if "severity_score" not in storm_cols:
        conn.execute("ALTER TABLE storm_events ADD COLUMN severity_score REAL DEFAULT 0.0")
        logger.info("column_added", table="storm_events", col="severity_score")

    # Create index for fast severity-ranked queries
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_storm_events_severity "
        "ON storm_events(report_time_utc, severity_score DESC)"
    )

    # --- jobs: add review and referral tracking columns ---
    cursor = conn.execute("PRAGMA table_info(jobs)")
    job_cols = {row[1] for row in cursor.fetchall()}

    new_job_cols = {
        "review_requested_at": "TIMESTAMP",
        "review_requested_by": "TEXT",
        "referral_code": "TEXT",
        "referral_source": "TEXT",
    }
    for col, col_type in new_job_cols.items():
        if col not in job_cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
            logger.info("column_added", table="jobs", col=col)

    logger.info("migration_complete", version=20)
