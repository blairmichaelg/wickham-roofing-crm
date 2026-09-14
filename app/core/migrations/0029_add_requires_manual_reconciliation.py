"""Migration 0029: Add requires_manual_reconciliation column to jobs table.

Flag is set whenever parse_statement_of_loss or ESX parser encounters a line-item/header mismatch
or parse gap, demanding office review in Triage / Next Best Action.
"""

from __future__ import annotations

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0029_add_requires_manual_reconciliation")


def up(conn: sqlite3.Connection) -> None:
    """Add requires_manual_reconciliation flag to jobs."""
    logger.info("applying_migration", version=29, name="add_requires_manual_reconciliation")
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(jobs)")
    cols = [r[1] for r in cursor.fetchall()]
    if "requires_manual_reconciliation" not in cols:
        cursor.execute("ALTER TABLE jobs ADD COLUMN requires_manual_reconciliation INTEGER NOT NULL DEFAULT 0")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_reconciliation ON jobs(requires_manual_reconciliation)")
    logger.info("migration_29_applied_successfully")
