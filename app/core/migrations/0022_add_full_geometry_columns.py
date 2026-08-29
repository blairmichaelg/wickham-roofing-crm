"""
Migration 0022: Add full roof geometry and component columns to jobs table.
Persists drip edge, flashing, step flashing, total facets, pipe boots, vents, starter strip, and wall flashing.
"""

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0022_add_full_geometry_columns")


def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=22, name="add_full_geometry_columns")

    cursor = conn.execute("PRAGMA table_info(jobs)")
    job_cols = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("ev_drip_edge_lf", "REAL"),
        ("ev_flashing_lf", "REAL"),
        ("ev_step_flashing_lf", "REAL"),
        ("ev_total_facets", "INTEGER"),
        ("ev_pipe_boot_count", "INTEGER"),
        ("ev_vent_count", "INTEGER"),
        ("ev_starter_strip_lf", "REAL"),
        ("ev_flashing_wall_lf", "REAL"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in job_cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
            logger.info("column_added", table="jobs", col=col_name)

    logger.info("migration_complete", version=22)
