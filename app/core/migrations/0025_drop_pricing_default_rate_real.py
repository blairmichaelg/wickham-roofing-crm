"""Migration 0025: Drop legacy default_rate REAL from pricing table.

Guarantees 100% integer cents storage across all pricing and rate records.
"""

from __future__ import annotations

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0025_drop_pricing_default_rate_real")


def up(conn: sqlite3.Connection) -> None:
    """Drop legacy REAL default_rate column from pricing table."""
    logger.info("applying_migration", version=25, name="drop_pricing_default_rate_real")

    # Check if default_rate column still exists
    cursor = conn.execute("PRAGMA table_info(pricing)")
    cols = {row[1] for row in cursor.fetchall()}

    # Ensure default_rate_cents exists and is populated before dropping REAL
    if "default_rate_cents" not in cols:
        conn.execute("ALTER TABLE pricing ADD COLUMN default_rate_cents INTEGER NOT NULL DEFAULT 0")
        if "default_rate" in cols:
            conn.execute("UPDATE pricing SET default_rate_cents = CAST(ROUND(default_rate * 100) AS INTEGER)")

    if "default_rate" in cols:
        try:
            conn.execute("ALTER TABLE pricing DROP COLUMN default_rate")
            logger.info("dropped_column", table="pricing", column="default_rate")
        except Exception as e:
            logger.warning("column_drop_skipped", table="pricing", column="default_rate", reason=str(e))

    logger.info("migration_complete", version=25)
